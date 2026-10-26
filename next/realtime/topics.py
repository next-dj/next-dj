"""Request-scoped SSE subscriptions to realtime topics."""

from __future__ import annotations

import asyncio
import math
import time
from contextlib import aclosing
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Self, cast, override

from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.utils.functional import SimpleLazyObject, empty

from next.partial.sse import PatchStreamWriter, sse_heartbeat_seconds
from next.partial.stream import StreamEnd, StreamHello, StreamSkip, stream_hello
from next.utils import positive_number

from .access import Access, AccessCheck, TopicAuthorizer
from .attendance import Identifier, aattend, aattending, attend, attending
from .binding import request_subject, session_user, ticket_binding
from .delivery import transformed
from .errors import BrokerConnectionError
from .lifecycle import StreamLifecycle
from .manager import broker_manager
from .messages import validate_topics
from .negotiation import negotiate, wants_websocket
from .pool import run_detached
from .replay import acatch_up, catch_up, format_event_id, parse_event_id
from .roster import counted, presence_broker, require_presence, subscriber_snapshot
from .signals import UnsubscribeReason
from .tickets import import_callable


_TOPIC_DENIED = StreamEnd("auth")


if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Callable,
        Iterator,
        Mapping,
    )

    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser
    from django.http import HttpRequest, JsonResponse
    from django.http.response import HttpResponseBase

    from next.partial.envelope import Envelope
    from next.partial.stream import StreamPosition

    from .attendance import Attendance, Identify
    from .backends import BrokerBackend
    from .messages import Message, Position
    from .roster import Presence
    from .tickets import TopicTransform

    TopicItem = Message | Envelope | StreamHello | StreamSkip | StreamEnd | None


@dataclass(frozen=True, slots=True)
class _Callbacks:
    """The transform, authorizer and presence of a stream, each with its dotted path."""

    transform: TopicTransform | None
    transform_ref: str | None
    authorize: TopicAuthorizer | None
    authorize_ref: str | None
    presence: bool | Identify
    presence_ref: str | None

    @classmethod
    def of(
        cls,
        *,
        transform: TopicTransform | str | None,
        authorize: TopicAuthorizer | str | None,
        presence: bool | Identify | str,
    ) -> Self:
        """Import the callbacks given as dotted paths, keeping each path."""
        _require_presence_option(presence)
        return cls(
            transform=_imported(transform, "transform")
            if isinstance(transform, str)
            else transform,
            transform_ref=transform if isinstance(transform, str) else None,
            authorize=_imported(authorize, "authorize")
            if isinstance(authorize, str)
            else authorize,
            authorize_ref=authorize if isinstance(authorize, str) else None,
            presence=_imported(presence, "presence")
            if isinstance(presence, str)
            else presence,
            presence_ref=presence if isinstance(presence, str) else None,
        )


class TopicStream(PatchStreamWriter):
    """Stream broker topics as SSE once the page guards and dependencies have run.

    A WebSocket negotiation renders into the JSON ticket reply, as Django serves it.
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
        callbacks = _Callbacks.of(
            transform=transform, authorize=authorize, presence=presence
        )
        self._callbacks = callbacks
        self._reply: JsonResponse | None = None
        self._negotiating = wants_websocket(request)
        if self._negotiating:
            self.topics = topics
            super().__init__(request, _no_items(request))
            self._stream_headers = frozenset(self.headers)
            return
        self._transform = callbacks.transform
        self._authorize = callbacks.authorize
        self._presence = callbacks.presence
        self._identifier = (
            None if isinstance(self._presence, bool) else Identifier.of(self._presence)
        )
        self._server_clock = server_clock if server_clock is not None else time.time
        selected = validate_topics(topics, at_least_one=True)
        interval = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else sse_heartbeat_seconds()
        )
        if not positive_number(interval, math.inf):
            msg = "TopicStream heartbeat_seconds must be a positive finite number."
            raise ValueError(msg)
        self.topics = selected
        self._single = len(selected) == 1
        self._subscription_timeout = interval
        session_key = _session_key(request)
        self._user = _stream_user(request, session_key)
        self._access = AccessCheck(
            selected,
            authorizer=self._authorize,
            session_key=session_key,
            user=self._user,
            interval=interval,
        )
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

    def render(self) -> HttpResponseBase:
        """Return the response Django serves, the ticket reply for a negotiation.

        Django renders any view response that has `render`, as a `TemplateResponse`.
        """
        if not self._negotiating:
            return self
        if self._reply is None:
            callbacks = self._callbacks
            reply = negotiate(
                self._request,
                self.topics,
                transform=(callbacks.transform, callbacks.transform_ref),
                authorize=(callbacks.authorize, callbacks.authorize_ref),
                presence=(callbacks.presence, callbacks.presence_ref),
            )
            for name, value in self.headers.items():
                if name not in self._stream_headers:
                    reply.headers.setdefault(name, value)
            self._reply = reply
        return self._reply

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
            self._identifier,
            self._user,
            heartbeat=self._subscription_timeout,
            subject=self._subject,
        )

    async def aattend(self, backend: BrokerBackend) -> Attendance | None:
        """Return the attendance of a presence stream through the asynchronous API."""
        if self._presence is False:
            return None
        return await aattend(
            backend,
            self.topics,
            self._identifier,
            self._user,
            heartbeat=self._subscription_timeout,
            subject=self._subject,
        )

    def _sync_item(self, message: Message | None) -> Message | StreamSkip | None:
        """Transform one message in this thread, or acknowledge its skip.

        An unnumbered snapshot skips the transform, it has no seq to acknowledge.
        """
        if message is None:
            return None
        if message.seq and self._transform is not None:
            return self._item(message, transformed(message, self._transform(message)))
        return self._item(message, message)

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
            output = await run_detached(self._transform, message)
            return self._item(message, transformed(message, output))
        return self._item(message, message)

    def _item(self, message: Message, result: Message | None) -> Message | StreamSkip:
        """Return what this stream sends for a message, the skip of its seq for None."""
        if result is None:
            return StreamSkip(message.topic, message.seq, message.epoch)
        return result if self._identifier is not None else counted(result)

    def _subject(self) -> str:
        """Return the request's opaque subject, read only when identify takes it."""
        return request_subject(self._request)

    def _snapshots(self, read: Callable[[str], Presence]) -> list[Message]:
        """Return the unnumbered snapshots a new subscriber gets right after hello."""
        identified = self._identifier is not None
        now = self._server_clock()
        return [
            subscriber_snapshot(topic, read(topic), identified=identified, now=now)
            for topic in self.topics
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


def _no_items(request: HttpRequest) -> Iterator[TopicItem] | AsyncIterator[TopicItem]:
    """Return an empty source of the kind the server reads, for a negotiation."""
    if isinstance(request, ASGIRequest):
        return _NoAsyncItems()
    return iter(())


class _NoAsyncItems:
    """An async source that ends at once, for a stream that is never served."""

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> TopicItem:
        raise StopAsyncIteration


def _stream_user(request: HttpRequest, session_key: str | None) -> object:
    """Return the request's user, an unloaded lazy one swapped for the stored session's.

    The request gets the swap too, as loading Django's lazy user may rotate the key.
    """
    user = getattr(request, "user", None)
    if (
        session_key is None
        or not isinstance(user, SimpleLazyObject)
        or user._wrapped is not empty
    ):
        return user
    baseline = SimpleLazyObject(partial(_stored_user, session_key))
    request.user = cast("AbstractBaseUser | AnonymousUser", baseline)
    return baseline


def _stored_user(session_key: str) -> object:
    """Load the user of a stored session without writing to it, anonymous once gone."""
    live = ticket_binding().live_session(session_key)
    return session_user(SessionBase()) if live is None else live.user


def _session_key(request: HttpRequest) -> str | None:
    """Return the request's persisted session key, read without loading the session."""
    key = getattr(getattr(request, "session", None), "session_key", None)
    return key if isinstance(key, str) and key else None


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


__all__ = ["TopicStream"]
