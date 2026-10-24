"""Authenticated Channels consumers for the realtime topic protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, cast, override

from channels.generic import websocket  # type: ignore[import-untyped]
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from next.partial.manager import partial_backend_manager
from next.partial.stream import StreamHello, StreamMeta, StreamSkip
from next.realtime.access import Access, AccessCheck, heartbeat_seconds, run_detached
from next.realtime.delivery import _prepare
from next.realtime.errors import BrokerConnectionError, InvalidTicketError
from next.realtime.manager import broker_manager
from next.realtime.tickets import TicketClaims, verify_ticket


_SUB_TIMEOUT = 5
_MAX_LAST_SEQ = 64
_SSE_LINE = re.compile(r"\r\n|\r|\n")
# 4401 sends the client to SSE, where the page guard rejudges the session cookie.
# 4403 is a topic denial that no reconnect can fix.
_CLOSE_CODES = {Access.SESSION_REVOKED: 4401, Access.TOPIC_DENIED: 4403}
logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

    from next.partial.envelope import Envelope
    from next.partial.patches import Patches
    from next.realtime.access import TopicAuthorizer
    from next.realtime.messages import Message

    TopicTransform = Callable[[Message], Patches | Envelope | None]


class RealtimeConsumer(websocket.AsyncJsonWebsocketConsumer):
    """Accept a signed, session-bound topic grant as the first WebSocket frame."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Start with no child tasks, so every exit path can clean up uniformly."""
        super().__init__(*args, **kwargs)
        self._task: asyncio.Task[None] | None = None
        self._first_frame_timeout: asyncio.Task[None] | None = None
        self._failure_close: asyncio.Task[None] | None = None
        self._subscribed = False
        self._closed = False
        self._stopped = False
        self._session_key = ""
        self._session_expires_at = 0.0
        self._heartbeat = heartbeat_seconds()

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        """Run the socket and cancel and await every child task however it ends."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._stop_children()

    async def connect(self) -> None:
        """Reject missing session middleware before accepting the socket."""
        session = self.scope.get("session")
        session_key = getattr(session, "session_key", None)
        if session is None or not isinstance(session_key, str) or not session_key:
            await self.close(code=4401)
            return
        age = await run_detached(session.get_expiry_age)
        if age <= 0:
            await self.close(code=4401)
            return
        self._session_key = session_key
        self._session_expires_at = time.monotonic() + age
        await self.accept()
        self._first_frame_timeout = asyncio.create_task(self._close_if_unsubscribed())

    @override
    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
        **kwargs: object,
    ) -> None:
        """Close with a protocol error on a binary, empty or non-JSON frame."""
        if self._closed:
            return
        if bytes_data is not None or not text_data:
            await self._protocol_error()
            return
        try:
            content = json.loads(text_data)
        except (ValueError, RecursionError):
            await self._protocol_error()
            return
        await self.receive_json(content, **kwargs)

    async def receive_json(self, content: object, **kwargs: object) -> None:
        """Validate the first subscription frame and answer control pongs."""
        if not self._subscribed:
            await self._subscribe(content)
            return
        if content == {"type": "pong"}:
            return
        await self.send_json({"type": "error", "code": "unsupported"})

    @override
    async def close(
        self, code: int | bool | None = None, reason: str | None = None
    ) -> None:
        """Close once and ignore every frame that arrives afterwards."""
        self._closed = True
        await super().close(code=code, reason=reason)

    async def disconnect(self, close_code: int) -> None:
        """Cancel a blocked broker read and await its subscription cleanup."""
        _ = close_code
        await self._stop_children()

    async def _stop_children(self) -> None:
        """Cancel the consumer's own tasks and wait until each has finished."""
        self._stopped = True
        current = asyncio.current_task()
        children = {
            task
            for task in (self._first_frame_timeout, self._task, self._failure_close)
            if task is not None and task is not current
        }
        for task in children:
            task.cancel()
        if children:
            await asyncio.wait(children)

    async def _subscribe(self, frame: object) -> None:
        if not isinstance(frame, Mapping) or set(frame) - {
            "type",
            "ticket",
            "last_seq",
        }:
            await self._protocol_error()
            return
        if frame.get("type") != "sub" or not _valid_cursor(frame.get("last_seq")):
            await self._protocol_error()
            return
        try:
            claims = verify_ticket(frame.get("ticket"), self.scope["session"])
            transform = self._resolve_transform(claims)
            authorizer = self._resolve_authorizer(claims)
        except (InvalidTicketError, ImportError, AttributeError, TypeError):
            await self.send_json({"type": "error", "code": "auth"})
            await self.close(code=4401)
            return
        if self._first_frame_timeout is not None:
            self._first_frame_timeout.cancel()
        self._subscribed = True
        access = AccessCheck(
            claims.topics,
            authorizer=authorizer,
            session_key=self._session_key,
            user=self.scope.get("user"),
            interval=self._heartbeat,
            expires_at=self._session_expires_at,
        )
        self._task = asyncio.create_task(self._serve(claims, transform, access))
        self._task.add_done_callback(self._serve_finished)

    def _resolve_transform(self, claims: TicketClaims) -> TopicTransform | None:
        if claims.transform_ref is None:
            return None
        transform = import_string(claims.transform_ref)
        if not callable(transform):
            msg = "The ticket transform is not callable."
            raise TypeError(msg)
        return cast("TopicTransform", transform)

    def _resolve_authorizer(self, claims: TicketClaims) -> TopicAuthorizer:
        authorizer = import_string(claims.authorizer_ref)
        if not callable(authorizer):
            msg = "The ticket authorizer is not callable."
            raise TypeError(msg)
        return cast("TopicAuthorizer", authorizer)

    async def _serve(
        self,
        claims: TicketClaims,
        transform: TopicTransform | None,
        access: AccessCheck,
    ) -> None:
        """Send hello and topic events until the client or session disconnects."""
        try:
            if not await self._granted(access):
                return
            backend = await broker_manager.aget()
            async with backend.asubscribe(
                claims.topics, timeout=self._heartbeat
            ) as messages:
                positions = await backend.apositions(claims.topics)
                hello = StreamHello(
                    server_time=_now_ms(),
                    topics={
                        topic: (positions[topic].epoch, positions[topic].seq)
                        for topic in claims.topics
                    },
                    transports=("ws", "sse"),
                )
                await self.send_json(
                    {"type": "hello", **hello.as_dict(), "heartbeat": self._heartbeat}
                )
                async for message in messages:
                    if not await self._granted(access):
                        return
                    await self._deliver_message(message, transform)
        except BrokerConnectionError:
            logger.info("Realtime WebSocket stream lost its broker subscription")
            await self.close(code=1011)
        except OSError:
            logger.warning("Realtime WebSocket broker connection failed", exc_info=True)
            await self.close(code=1011)

    def _serve_finished(self, task: asyncio.Task[None]) -> None:
        """Log an unexpected stream failure and close the socket with 1011."""
        if self._stopped or task.cancelled() or task.exception() is None:
            return
        logger.error("Realtime WebSocket stream failed", exc_info=task.exception())
        self._failure_close = asyncio.ensure_future(self.close(code=1011))

    async def _granted(self, access: AccessCheck) -> bool:
        verdict = await access.acheck()
        if verdict is Access.GRANTED:
            return True
        await self.send_json({"type": "error", "code": "auth"})
        await self.close(code=_CLOSE_CODES[verdict])
        return False

    async def _deliver_message(
        self, message: Message | None, transform: TopicTransform | None
    ) -> None:
        if message is None:
            await self.send_json({"type": "ping", "server_time": _now_ms()})
            return
        if transform is None:
            await self._send_message(message)
            return
        result = await run_detached(transform, message)
        if result is None:
            skip = StreamSkip(message.topic, message.seq, message.epoch)
            await self.send_json({"type": "skip", **skip.as_dict()})
            return
        await self._send_message(replace(message, envelope=_prepare(result)))

    async def _send_message(self, message: Message) -> None:
        """Send the exact SSE data text of the backend's frame, never re-parsed."""
        frame = partial_backend_manager.get().sse_stream_event(
            message.envelope,
            StreamMeta(
                server_time=_now_ms(),
                topic=message.topic,
                seq=message.seq,
                epoch=message.epoch,
            ),
        )
        data = _sse_data(frame)
        if data is None:
            msg = "A partial backend must emit an SSE data field for WebSocket streams."
            raise ImproperlyConfigured(msg)
        await self.send_json({"type": "patches", "data": data})

    async def _close_if_unsubscribed(self) -> None:
        await asyncio.sleep(_SUB_TIMEOUT)
        if not self._subscribed:
            await self._protocol_error()

    async def _protocol_error(self) -> None:
        await self.send_json({"type": "error", "code": "protocol"})
        await self.close(code=4400)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sse_data(frame: str) -> str | None:
    """Join the `data:` field lines of one SSE frame the way an EventSource does."""
    values = [
        line[6:] if line.startswith("data: ") else line[5:]
        for line in _SSE_LINE.split(frame)
        if line.startswith("data:")
    ]
    return "\n".join(values) if values else None


def _valid_cursor(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or len(value) > _MAX_LAST_SEQ:
        return False
    return all(
        isinstance(topic, str) and _valid_position(position)
        for topic, position in value.items()
    )


def _valid_position(value: object) -> bool:
    """Accept a bare sequence or an `{epoch, seq}` cursor entry."""
    if isinstance(value, Mapping):
        return (
            set(value) == {"epoch", "seq"}
            and isinstance(value["epoch"], str)
            and _valid_seq(value["seq"])
        )
    return _valid_seq(value)


def _valid_seq(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


__all__ = ["RealtimeConsumer"]
