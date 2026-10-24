"""Request-scoped SSE subscriptions to realtime topics."""

from __future__ import annotations

import json
import math
import time
from contextlib import suppress
from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING, TypeGuard, cast

from django.core.handlers.asgi import ASGIRequest
from django.http import StreamingHttpResponse
from django.utils.cache import patch_vary_headers
from django.utils.module_loading import import_string

from next.partial.sse import PatchStreamWriter
from next.partial.stream import StreamEnd, StreamHello, StreamSkip

from .access import (
    Access,
    AccessCheck,
    TopicAuthorizer,
    heartbeat_seconds as realtime_heartbeat_seconds,
    run_detached,
)
from .delivery import _prepare
from .errors import BrokerConnectionError, InvalidTicketError, InvalidTopicError
from .manager import broker_manager
from .tickets import MAX_REFERENCE_LENGTH, issue_ticket
from .topics import validate_topic
from .transports import active_transports


_MAX_TOPICS = 64
_TOPIC_DENIED = StreamEnd("auth")


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping

    from django.http import HttpRequest

    from next.partial.envelope import Envelope
    from next.partial.patches import Patches

    from .backends import BrokerBackend
    from .messages import Message, Position

    TopicTransform = Callable[[Message], Patches | Envelope | None]
    TopicItem = Message | Envelope | StreamHello | StreamSkip | StreamEnd | None


class TopicStream(PatchStreamWriter):
    """Stream broker topics as SSE, or answer a WebSocket negotiation with a ticket.

    A negotiation reply is still a `TopicStream`, a one-chunk `application/json` body.
    """

    def __init__(
        self,
        request: HttpRequest,
        *topics: str,
        transform: TopicTransform | None = None,
        transform_ref: str | None = None,
        authorize: TopicAuthorizer | None = None,
        authorize_ref: str | None = None,
        heartbeat_seconds: float | None = None,
        server_clock: Callable[[], float] | None = None,
    ) -> None:
        """Subscribe after the normal page guards and dependency resolution ran."""
        self._transform = transform
        self._transform_ref = transform_ref
        self._authorize = authorize
        self._authorize_ref = authorize_ref
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
        self._subscription_timeout = interval
        self._access = AccessCheck(
            selected,
            authorizer=authorize,
            session_key=_session_key(request),
            user=getattr(request, "user", None),
            interval=interval,
        )
        source: Iterator[TopicItem] | AsyncIterator[TopicItem]
        if isinstance(request, ASGIRequest):
            source = self._async_messages()
        else:
            source = self._sync_messages(broker_manager.get())
        super().__init__(
            request, source, heartbeat_seconds=interval, server_clock=self._server_clock
        )

    def _negotiate(self, request: HttpRequest) -> None:
        """Become the JSON ticket reply, or the reply that sends the client to SSE."""
        status, body, headers = _negotiate_websocket(
            request,
            self.topics,
            self._transform,
            self._transform_ref,
            self._authorize,
            self._authorize_ref,
        )
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

    def _sync_messages(self, backend: BrokerBackend) -> Iterator[TopicItem]:
        """Yield broker messages and heartbeats while access is still granted."""
        verdict = self._access.check()
        if verdict is not Access.GRANTED:
            yield from _closing_frames(verdict)
            return
        with suppress(BrokerConnectionError):
            yield from self._sync_subscription(backend)

    def _sync_subscription(self, backend: BrokerBackend) -> Iterator[TopicItem]:
        """Yield one subscription's hello and messages until access is revoked."""
        with backend.subscribe(
            self.topics, timeout=self._subscription_timeout
        ) as stream:
            yield self._hello(backend.positions(self.topics))
            for message in stream:
                verdict = self._access.check()
                if verdict is not Access.GRANTED:
                    yield from _closing_frames(verdict)
                    return
                if message is None:
                    yield None
                    continue
                result = self._transform_message(message)
                if result is not None:
                    yield result
                else:
                    yield StreamSkip(message.topic, message.seq, message.epoch)

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
        try:
            async with backend.asubscribe(
                self.topics, timeout=self._subscription_timeout
            ) as stream:
                yield self._hello(await backend.apositions(self.topics))
                async for message in stream:
                    verdict = await self._access.acheck()
                    if verdict is not Access.GRANTED:
                        for frame in _closing_frames(verdict):
                            yield frame
                        return
                    yield await self._async_item(message)
        except BrokerConnectionError:
            return

    async def _async_item(self, message: Message | None) -> Message | StreamSkip | None:
        """Transform one message off the event loop, or acknowledge its skip."""
        if message is None or self._transform is None:
            return message
        result = await run_detached(self._transform, message)
        if result is None:
            return StreamSkip(message.topic, message.seq, message.epoch)
        return replace(message, envelope=_prepare(result))

    def _hello(self, positions: Mapping[str, Position]) -> StreamHello:
        """Announce the subscription's starting position on every topic."""
        return StreamHello(
            server_time=int(self._server_clock() * 1000),
            topics={
                topic: (positions[topic].epoch, positions[topic].seq)
                for topic in self.topics
            },
        )

    def _transform_message(self, message: Message) -> Message | Envelope | None:
        """Apply the subscriber-local transform and validate its output."""
        if self._transform is None:
            return message
        result = self._transform(message)
        if result is None:
            return None
        return replace(message, envelope=_prepare(result))


def _closing_frames(verdict: Access) -> tuple[StreamEnd, ...]:
    """End for good only on a topic denial, so a session change reconnects instead.

    A reconnect carries the current cookie and lets the page guard decide.
    """
    return (_TOPIC_DENIED,) if verdict is Access.TOPIC_DENIED else ()


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


def _transform_reference(
    transform: TopicTransform | None, explicit: str | None
) -> str | None:
    """Return an import-stable dotted transform reference or disable WS filtering."""
    if transform is None:
        return None
    candidate = explicit
    if candidate is None:
        module = getattr(transform, "__module__", None)
        qualified = getattr(transform, "__qualname__", None)
        if (
            isinstance(module, str)
            and isinstance(qualified, str)
            and "<locals>" not in qualified
        ):
            candidate = f"{module}.{qualified}"
    if not _storable(candidate):
        return None
    try:
        return candidate if import_string(candidate) is transform else None
    except (ImportError, AttributeError, TypeError):
        return None


def _authorizer_reference(
    authorizer: TopicAuthorizer | None, explicit: str | None
) -> str | None:
    """Return an import-stable authorization callback for WebSocket grants."""
    if authorizer is None:
        return None
    candidate = explicit
    if candidate is None:
        module = getattr(authorizer, "__module__", None)
        qualified = getattr(authorizer, "__qualname__", None)
        if (
            isinstance(module, str)
            and isinstance(qualified, str)
            and "<locals>" not in qualified
        ):
            candidate = f"{module}.{qualified}"
    if not _storable(candidate):
        return None
    try:
        return candidate if import_string(candidate) is authorizer else None
    except (ImportError, AttributeError, TypeError):
        return None


def _storable(candidate: str | None) -> TypeGuard[str]:
    """Whether a dotted reference fits the signed ticket."""
    return isinstance(candidate, str) and 0 < len(candidate) <= MAX_REFERENCE_LENGTH


def _negotiate_websocket(
    request: HttpRequest,
    topics: tuple[str, ...],
    raw_transform: object,
    raw_transform_ref: object,
    raw_authorizer: object,
    raw_authorizer_ref: object,
) -> tuple[int, dict[str, object], dict[str, str]]:
    """Return the status, JSON body and headers of a ticket or SSE fallback reply."""
    if getattr(request, "method", "GET") != "GET":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method"}, {"Allow": "GET"}
    if "ws" not in active_transports():
        return HTTPStatus.NOT_ACCEPTABLE, {"error": "transport_unavailable"}, {}
    selected = _grantable_topics(topics)
    if selected is None:
        return HTTPStatus.BAD_REQUEST, {"error": "invalid_topics"}, {}
    refs = _ticket_references(
        raw_transform, raw_transform_ref, raw_authorizer, raw_authorizer_ref
    )
    if isinstance(refs, str):
        return HTTPStatus.NOT_ACCEPTABLE, {"transport": "sse", "reason": refs}, {}
    transform_ref, authorizer_ref = refs
    try:
        grant = issue_ticket(
            request,
            selected,
            transform_ref=transform_ref,
            authorizer_ref=authorizer_ref,
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
    raw_transform: object,
    raw_transform_ref: object,
    raw_authorizer: object,
    raw_authorizer_ref: object,
) -> tuple[str | None, str] | str:
    """Require import-stable filtering and authorization, or name the SSE reason."""
    transform = cast("TopicTransform | None", raw_transform)
    transform_ref = raw_transform_ref if isinstance(raw_transform_ref, str) else None
    resolved_ref = _transform_reference(transform, transform_ref)
    if transform is not None and resolved_ref is None:
        return "transform"
    authorizer = cast("TopicAuthorizer | None", raw_authorizer)
    authorizer_ref = raw_authorizer_ref if isinstance(raw_authorizer_ref, str) else None
    resolved_authorizer = _authorizer_reference(authorizer, authorizer_ref)
    if resolved_authorizer is None:
        return "authorization"
    return resolved_ref, resolved_authorizer


__all__ = ["TopicStream"]
