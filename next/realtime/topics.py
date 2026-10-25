"""Request-scoped SSE subscriptions to realtime topics and their WebSocket handoff."""

from __future__ import annotations

import asyncio
import json
import math
import time
from contextlib import aclosing
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, TypeGuard, override

from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.http import StreamingHttpResponse
from django.utils.cache import patch_vary_headers
from django.utils.module_loading import import_string

from next.partial.sse import PatchStreamWriter
from next.partial.stream import StreamEnd, StreamHello, StreamSkip, stream_hello

from .access import (
    Access,
    AccessCheck,
    TopicAuthorizer,
    heartbeat_seconds as realtime_heartbeat_seconds,
    run_detached,
)
from .attendance import aattend, aattending, attend, attending
from .binding import ticket_binding
from .delivery import transformed
from .errors import BrokerConnectionError, InvalidTicketError, InvalidTopicError
from .manager import broker_manager
from .messages import validate_topic
from .replay import acatch_up, catch_up, format_event_id, parse_event_id
from .roster import (
    Presence,
    counted,
    presence_broker,
    require_presence,
    snapshot_message,
)
from .signals import StreamLifecycle, UnsubscribeReason
from .tickets import MAX_REFERENCE_LENGTH, import_callable, issue_ticket
from .transports import active_transports


_MAX_TOPICS = 64
_TOPIC_DENIED = StreamEnd("auth")


if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Callable,
        Iterator,
        Mapping,
    )

    from django.http import HttpRequest

    from next.partial.envelope import Envelope
    from next.partial.stream import StreamPosition

    from .attendance import Attendance, Identify
    from .backends import BrokerBackend
    from .messages import Message, Position
    from .tickets import TopicTransform

    TopicItem = Message | Envelope | StreamHello | StreamSkip | StreamEnd | None


class TopicStream(PatchStreamWriter):
    """Stream broker topics as SSE, or answer a WebSocket negotiation with a ticket.

    A negotiation reply is still a `TopicStream`, a one-chunk `application/json` body.
    """

    def __init__(
        self,
        request: HttpRequest,
        *topics: str,
        transform: TopicTransform | str | None = None,
        authorize: TopicAuthorizer | str | None = None,
        presence: bool | Identify | str = False,
        heartbeat_seconds: float | None = None,
        server_clock: Callable[[], float] | None = None,
    ) -> None:
        """Subscribe after the normal page guards and dependency resolution ran.

        A dotted path stands for its callable and reaches a WebSocket ticket verbatim.
        """
        self._transform_ref = transform if isinstance(transform, str) else None
        self._transform: TopicTransform | None = (
            _imported(transform, "transform")
            if isinstance(transform, str)
            else transform
        )
        self._authorize_ref = authorize if isinstance(authorize, str) else None
        self._authorize: TopicAuthorizer | None = (
            _imported(authorize, "authorize")
            if isinstance(authorize, str)
            else authorize
        )
        _require_presence_option(presence)
        self._presence_ref = presence if isinstance(presence, str) else None
        self._presence: bool | Identify = (
            _imported(presence, "presence") if isinstance(presence, str) else presence
        )
        self._identifies = not isinstance(self._presence, bool)
        self._server_clock = server_clock if server_clock is not None else time.time
        if _wants_websocket(request):
            self.topics = topics
            self._negotiate(request)
            return
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
        if not selected:
            msg = "TopicStream requires at least one topic."
            raise ValueError(msg)
        interval = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else realtime_heartbeat_seconds()
        )
        if (
            not isinstance(interval, int | float)
            or isinstance(interval, bool)
            or not math.isfinite(interval)
            or interval <= 0
        ):
            msg = "TopicStream heartbeat_seconds must be a positive finite number."
            raise ValueError(msg)
        self.topics = selected
        self._single = len(selected) == 1
        self._subscription_timeout = interval
        self._access = AccessCheck(
            selected,
            authorizer=self._authorize,
            session_key=_session_key(request),
            user=getattr(request, "user", None),
            interval=interval,
        )
        self._user = getattr(request, "user", None)
        source: Iterator[TopicItem] | AsyncIterator[TopicItem]
        if isinstance(request, ASGIRequest):
            if presence is not False:
                require_presence(broker_manager.backend_class())
            source = self._async_messages()
        else:
            backend = broker_manager.get()
            if presence is not False:
                presence_broker(backend)
            source = self._sync_messages(backend)
        super().__init__(
            request, source, heartbeat_seconds=interval, server_clock=self._server_clock
        )
        self._cursors = self.replay_cursors(request)

    def _negotiate(self, request: HttpRequest) -> None:
        """Become the JSON ticket reply, or the reply that sends the client to SSE."""
        refs = _ticket_references(
            (self._transform, self._transform_ref),
            (self._authorize, self._authorize_ref),
            (self._presence, self._presence_ref),
        )
        status, body, headers = _negotiate_websocket(request, self.topics, refs)
        payload = json.dumps(body, separators=(",", ":")).encode()
        content: Iterator[bytes] | AsyncIterator[bytes] = (
            _single_chunk(payload)
            if isinstance(request, ASGIRequest)
            else iter((payload,))
        )
        StreamingHttpResponse.__init__(
            self, content, content_type="application/json", status=status
        )
        for header, value in headers.items():
            self[header] = value
        if status == HTTPStatus.OK:
            patch_vary_headers(self, ["Cookie", "X-Next-Transport"])
            ticket_binding().respond(request, self)

    def _sync_messages(self, backend: BrokerBackend) -> Iterator[TopicItem]:
        """Yield broker messages and heartbeats while access is still granted."""
        verdict = self._access.check()
        if verdict is not Access.GRANTED:
            yield from _closing_frames(verdict)
            return
        lifecycle = StreamLifecycle(self, "sse")
        reason = UnsubscribeReason.SERVER
        try:
            yield from self._sync_subscription(backend, lifecycle)
        except BrokerConnectionError:
            reason = UnsubscribeReason.ERROR
        except GeneratorExit:
            reason = UnsubscribeReason.CLIENT
            raise
        except Exception:
            reason = UnsubscribeReason.ERROR
            raise
        finally:
            lifecycle.close(reason)

    def _sync_subscription(
        self, backend: BrokerBackend, lifecycle: StreamLifecycle
    ) -> Iterator[TopicItem]:
        """Yield replayed misses, hello and live messages until access is revoked."""
        with backend.subscribe(
            self.topics, timeout=self._subscription_timeout
        ) as subscription:
            lifecycle.open(self.topics, request=self._request, user=self._user)
            positions = backend.positions(self.topics)
            attendance = self.attend(backend)
            with attending(attendance, subscription) as stream:
                for missed in catch_up(backend, self._cursors, positions):
                    verdict = self._access.check()
                    if verdict is not Access.GRANTED:
                        yield from _revoked(verdict, lifecycle)
                        return
                    yield self._sync_item(missed)
                yield self._hello(positions)
                if attendance is not None:
                    yield from self._snapshots(attendance.broker.presence)
                for message in stream:
                    verdict = self._access.check()
                    if verdict is not Access.GRANTED:
                        yield from _revoked(verdict, lifecycle)
                        return
                    yield self._sync_item(message)

    def attend(self, backend: BrokerBackend) -> Attendance | None:
        """Return the attendance a presence stream keeps on its topics, None without.

        An override returns another `Attendance`, a subclass with its own TTL too.
        """
        if self._presence is False:
            return None
        return attend(
            backend,
            self.topics,
            self._identify(),
            self._user,
            heartbeat=self._subscription_timeout,
        )

    async def aattend(self, backend: BrokerBackend) -> Attendance | None:
        """Return the attendance of a presence stream through the asynchronous API."""
        if self._presence is False:
            return None
        return await aattend(
            backend,
            self.topics,
            self._identify(),
            self._user,
            heartbeat=self._subscription_timeout,
        )

    def _sync_item(self, message: Message | None) -> Message | StreamSkip | None:
        """Transform one message in this thread, or acknowledge its skip.

        An unnumbered snapshot skips the transform, it has no seq to acknowledge.
        """
        if message is None:
            return None
        if message.seq:
            result = self._transform_message(message)
            if result is None:
                return StreamSkip(message.topic, message.seq, message.epoch)
            message = result
        return message if self._identifies else counted(message)

    async def _async_messages(
        self, backend: BrokerBackend | None = None
    ) -> AsyncIterator[TopicItem]:
        """Yield async broker messages and heartbeats while access is still granted."""
        verdict = await self._access.acheck()
        if verdict is not Access.GRANTED:
            for frame in _closing_frames(verdict):
                yield frame
            return
        if backend is None:
            backend = await broker_manager.aget()
        lifecycle = StreamLifecycle(self, "sse")
        reason = UnsubscribeReason.SERVER
        try:
            async with aclosing(self._async_subscription(backend, lifecycle)) as items:
                async for item in items:
                    yield item
        except BrokerConnectionError:
            reason = UnsubscribeReason.ERROR
        except (GeneratorExit, asyncio.CancelledError):
            reason = UnsubscribeReason.CLIENT
            raise
        except Exception:
            reason = UnsubscribeReason.ERROR
            raise
        finally:
            await lifecycle.aclose(reason)

    async def _async_subscription(
        self, backend: BrokerBackend, lifecycle: StreamLifecycle
    ) -> AsyncGenerator[TopicItem]:
        """Yield replayed misses, hello and live messages until access is revoked."""
        async with backend.asubscribe(
            self.topics, timeout=self._subscription_timeout
        ) as subscription:
            await lifecycle.aopen(self.topics, request=self._request, user=self._user)
            positions = await backend.apositions(self.topics)
            attendance = await self.aattend(backend)
            async with aattending(attendance, subscription) as stream:
                for missed in await acatch_up(backend, self._cursors, positions):
                    verdict = await self._access.acheck()
                    if verdict is not Access.GRANTED:
                        for frame in _revoked(verdict, lifecycle):
                            yield frame
                        return
                    yield await self._async_item(missed)
                yield self._hello(positions)
                for snapshot in await self._asnapshots(attendance):
                    yield snapshot
                async for message in stream:
                    verdict = await self._access.acheck()
                    if verdict is not Access.GRANTED:
                        for frame in _revoked(verdict, lifecycle):
                            yield frame
                        return
                    yield await self._async_item(message)

    async def _async_item(self, message: Message | None) -> Message | StreamSkip | None:
        """Transform one message off the event loop, or acknowledge its skip."""
        if message is None:
            return None
        if message.seq and self._transform is not None:
            result = transformed(message, await run_detached(self._transform, message))
            if result is None:
                return StreamSkip(message.topic, message.seq, message.epoch)
            message = result
        return message if self._identifies else counted(message)

    def _identify(self) -> Identify | None:
        """Return the member callable, None when the stream only counts."""
        presence = self._presence
        return None if isinstance(presence, bool) else presence

    def _snapshots(self, read: Callable[[str], Presence]) -> list[Message]:
        """Return the unnumbered snapshots a new subscriber gets right after hello."""
        identified = self._identifies
        now = self._server_clock()
        return [
            snapshot_message(
                topic, snapshot if identified else Presence(snapshot.count), now
            )
            for topic, snapshot in ((topic, read(topic)) for topic in self.topics)
        ]

    async def _asnapshots(self, attendance: Attendance | None) -> list[Message]:
        """Read the snapshots after hello through the asynchronous API."""
        if attendance is None:
            return []
        broker = attendance.broker
        read = {topic: await broker.apresence(topic) for topic in self.topics}
        return self._snapshots(read.__getitem__)

    def replay_cursors(self, request: HttpRequest) -> Mapping[str, Position]:
        """Return the cursor a single-topic stream resumes from, its `Last-Event-ID`.

        A malformed id or a multi-topic stream resumes from nothing and resyncs.
        """
        if len(self.topics) != 1:
            return {}
        cursor = parse_event_id(request.headers.get("Last-Event-ID"))
        return {} if cursor is None else {self.topics[0]: cursor}

    @override
    def event_id(self, frame: StreamPosition | StreamHello) -> str | None:
        """Return the `<epoch>:<seq>` id `EventSource` resends as `Last-Event-ID`.

        Hello carries the topic position, so a reconnect before any message resumes.
        """
        if not self._single:
            return None
        if isinstance(frame, StreamHello):
            epoch, seq = frame.topics[self.topics[0]]
            return format_event_id(epoch, seq)
        return format_event_id(frame.epoch, frame.seq)

    def _hello(self, positions: Mapping[str, Position]) -> StreamHello:
        """Announce the subscription's starting position on every topic."""
        return stream_hello(
            self.topics,
            positions,
            server_time=int(self._server_clock() * 1000),
            transports=("sse",),
        )

    def _transform_message(self, message: Message) -> Message | None:
        """Apply the subscriber-local transform and validate its output."""
        if self._transform is None:
            return message
        return transformed(message, self._transform(message))


def _closing_frames(verdict: Access) -> tuple[StreamEnd, ...]:
    """End for good only on a topic denial, so a session change reconnects instead.

    A reconnect carries the current cookie and lets the page guard decide.
    """
    return (_TOPIC_DENIED,) if verdict is Access.TOPIC_DENIED else ()


def _revoked(verdict: Access, lifecycle: StreamLifecycle) -> tuple[StreamEnd, ...]:
    """Note a revocation as the stream's unsubscribe reason and return its frames."""
    lifecycle.reason = UnsubscribeReason.REVOKED
    return _closing_frames(verdict)


def _require_presence_option(presence: object) -> None:
    """Refuse a `presence=` that is no bool, identify callable or dotted path."""
    if not (isinstance(presence, bool | str) or callable(presence)):
        msg = (
            "TopicStream presence= takes a bool, an identify callable or its "
            f"dotted path, not {presence!r}."
        )
        raise ImproperlyConfigured(msg)


def _wants_websocket(request: HttpRequest) -> bool:
    """Whether the client asked to negotiate the WebSocket transport."""
    headers = getattr(request, "headers", {})
    return str(headers.get("X-Next-Transport", "")).lower() == "ws"


def _session_key(request: HttpRequest) -> str | None:
    """Return the request's persisted session key, read without loading the session."""
    key = getattr(getattr(request, "session", None), "session_key", None)
    return key if isinstance(key, str) and key else None


async def _single_chunk(payload: bytes) -> AsyncIterator[bytes]:
    """Yield one body chunk, the async form an ASGI server streams without buffering."""
    yield payload


def _reference(callback: object, explicit: object) -> str | None:
    """Return the import-stable dotted path of a callback a ticket can name, or None."""
    if callback is None:
        return None
    candidate = explicit if isinstance(explicit, str) else None
    if candidate is None:
        module = getattr(callback, "__module__", None)
        qualified = getattr(callback, "__qualname__", None)
        if (
            isinstance(module, str)
            and isinstance(qualified, str)
            and "<locals>" not in qualified
        ):
            candidate = f"{module}.{qualified}"
    if not _storable(candidate):
        return None
    try:
        return candidate if import_string(candidate) is callback else None
    except (ImportError, AttributeError, TypeError):
        return None


def _imported(path: str, kind: str) -> Callable[..., Any]:
    """Import the callable a dotted path names, refusing the stream when it cannot."""
    try:
        return import_callable(path)
    except ImportError as exc:
        msg = f"TopicStream {kind}={path!r} does not import."
        raise ImproperlyConfigured(msg) from exc
    except TypeError as exc:
        msg = f"TopicStream {kind}={path!r} does not name a callable."
        raise ImproperlyConfigured(msg) from exc


def _storable(candidate: str | None) -> TypeGuard[str]:
    """Whether a dotted reference fits the signed ticket."""
    return isinstance(candidate, str) and 0 < len(candidate) <= MAX_REFERENCE_LENGTH


def _negotiate_websocket(
    request: HttpRequest,
    topics: tuple[str, ...],
    refs: tuple[str | None, str, bool | str] | str,
) -> tuple[int, dict[str, object], dict[str, str]]:
    """Return the status, JSON body and headers of a ticket or SSE fallback reply."""
    if getattr(request, "method", "GET") != "GET":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method"}, {"Allow": "GET"}
    if "ws" not in active_transports():
        return HTTPStatus.NOT_ACCEPTABLE, {"error": "transport_unavailable"}, {}
    selected = _grantable_topics(topics)
    if selected is None:
        return HTTPStatus.BAD_REQUEST, {"error": "invalid_topics"}, {}
    if isinstance(refs, str):
        return HTTPStatus.NOT_ACCEPTABLE, {"transport": "sse", "reason": refs}, {}
    transform_ref, authorizer_ref, presence = refs
    try:
        grant = issue_ticket(
            request,
            selected,
            transform_ref=transform_ref,
            authorizer_ref=authorizer_ref,
            presence=presence,
        )
    except InvalidTicketError:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "session_unavailable"}, {}
    return HTTPStatus.OK, grant.as_dict(), {"Cache-Control": "private, no-store"}


def _grantable_topics(topics: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the unique valid topics a ticket can grant, or None."""
    try:
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
    except InvalidTopicError:
        return None
    if not selected or len(selected) > _MAX_TOPICS:
        return None
    return selected


def _ticket_references(
    transform: tuple[object, object],
    authorizer: tuple[object, object],
    presence: tuple[object, object],
) -> tuple[str | None, str, bool | str] | str:
    """Require import-stable filtering, authorization and identify, or name the reason.

    Each argument pairs a callback with the dotted path it was given as, if any.
    """
    resolved_ref = _reference(*transform)
    if transform[0] is not None and resolved_ref is None:
        return "transform"
    resolved_authorizer = _reference(*authorizer)
    if resolved_authorizer is None:
        return "authorization"
    mode, identify_ref = presence
    if isinstance(mode, bool):
        return resolved_ref, resolved_authorizer, mode
    resolved_identify = _reference(mode, identify_ref)
    if resolved_identify is None:
        return "presence"
    return resolved_ref, resolved_authorizer, resolved_identify


__all__ = ["TopicStream"]
