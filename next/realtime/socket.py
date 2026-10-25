"""The realtime topic protocol over any WebSocket, spoken through a two-method port."""

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, NoReturn, Protocol, Self

from next.partial.manager import partial_backend_manager

from . import frames
from .access import Access, AccessCheck, heartbeat_seconds, live_session, run_detached
from .backends import BrokerBackend
from .delivery import transformed
from .errors import BrokerConnectionError, InvalidTicketError
from .frames import Close, FrameError, Pong, Subscribe
from .manager import broker_manager
from .messages import Message, Position
from .replay import acatch_up
from .tickets import TicketClaims, resolve_claims, verify_ticket


if TYPE_CHECKING:
    from .tickets import TopicTransform


logger = logging.getLogger(__name__)

_CLOSE_CODES = {
    Access.SESSION_REVOKED: Close.SESSION,
    Access.TOPIC_DENIED: Close.TOPIC_DENIED,
}
_REASONS = {
    Close.PROTOCOL: "protocol",
    Close.SESSION: "auth",
    Close.TOPIC_DENIED: "auth",
}


class SocketPort(Protocol):
    """The two operations a `TopicSocket` needs from an accepted WebSocket.

    `send` raises `OSError` once the peer is gone, and `close` must tolerate that.
    """

    async def send(self, text: str) -> None:
        """Send one text frame."""

    async def close(self, code: int) -> None:
        """Close the WebSocket with a close code."""


@dataclass(frozen=True, slots=True)
class Handshake:
    """The session a WebSocket was accepted for, the one its ticket was issued under.

    `expires_at` is a forced session recheck on the `time.monotonic()` clock, or None.
    """

    session_key: str
    user: object
    expires_at: float | None

    @classmethod
    async def from_session(cls, session_key: str) -> Self | None:
        """Load a stored session and its user on the shared pool, or None once gone.

        Nothing is written, so the load neither rotates the key nor extends the expiry.
        """
        live = await run_detached(live_session, session_key)
        if live is None:
            return None
        user, left = live
        expires_at = None if left is None else time.monotonic() + left
        return cls(session_key, user, expires_at)


class _ClosedError(Exception):
    """Unwind the connection, closing with `code` or dropping it when None."""

    def __init__(self, code: int | None) -> None:
        self.code = code
        super().__init__(code)


class _Writer:
    """The single path to the port, so concurrent frames never interleave a send."""

    __slots__ = ("_lock", "_port", "sending_since")

    def __init__(self, port: SocketPort) -> None:
        self._port = port
        self._lock = asyncio.Lock()
        self.sending_since: float | None = None

    async def send(self, text: str) -> None:
        """Send under the lock, dropping the connection when the peer is gone."""
        async with self._lock:
            self.sending_since = time.monotonic()
            try:
                await self._port.send(text)
            except OSError as exc:
                raise _ClosedError(None) from exc
            finally:
                self.sending_since = None

    def stalled(self, limit: float) -> bool:
        """Whether a send has been blocked by the peer for longer than `limit`."""
        since = self.sending_since
        return since is not None and time.monotonic() - since > limit

    async def close(self, code: int) -> None:
        """Close once the senders are done, ignoring a peer that already left."""
        async with self._lock:
            with suppress(OSError):
                await self._port.close(code)


class TopicSocket:
    """Serve one WebSocket the realtime topic protocol, from first frame to close.

    It knows no ASGI, so any server or gateway drives it through a `SocketPort`.
    """

    _access: AccessCheck

    def __init__(
        self,
        port: SocketPort,
        handshake: Handshake,
        *,
        broker: BrokerBackend | None = None,
        heartbeat: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind the connection to its port and session without touching the network."""
        self.handshake = handshake
        self.connection_id = secrets.token_urlsafe(16)
        self.cursor: Mapping[str, Position | int] = MappingProxyType({})
        self._writer = _Writer(port)
        self._broker = broker
        self._heartbeat = heartbeat if heartbeat is not None else heartbeat_seconds()
        self._clock = clock
        self._transform: TopicTransform | None = None

    async def serve(self, inbound: AsyncIterable[str | bytes]) -> None:
        """Run the connection until the client leaves or the protocol closes it."""
        codes: list[int | None] = []
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._read(inbound, group))
                group.create_task(self._watch())
        except* _ClosedError as closed:
            codes.extend(
                error.code
                for error in closed.exceptions
                if isinstance(error, _ClosedError)
            )
        except* BrokerConnectionError:
            logger.info("Realtime WebSocket stream lost its broker subscription")
            codes.append(Close.INTERNAL)
        except* OSError:
            logger.warning("Realtime WebSocket broker connection failed", exc_info=True)
            codes.append(Close.INTERNAL)
        except* Exception:
            logger.exception("Realtime WebSocket stream failed")
            codes.append(Close.INTERNAL)
        code = codes[0] if codes else None
        if code is not None and None not in codes:
            await self._writer.close(code)

    async def catch_up(
        self,
        subscription: AsyncIterator[Message | None],
        positions: Mapping[str, Position],
    ) -> None:
        """Replay what the client cursor missed before hello, nothing on a gap.

        Later messages wait in the subscription, passed so an override can read it.
        """
        del subscription
        cursors = {
            topic: cursor
            for topic, cursor in self.cursor.items()
            if isinstance(cursor, Position)
        }
        for message in await acatch_up(self._broker, cursors, positions):
            await self._grant()
            await self._writer.send(await self._frame_for(message))

    async def tick(self) -> None:
        """Answer an idle heartbeat of the subscription with a ping."""
        await self._writer.send(frames.ping(self._now()))

    async def _read(
        self, inbound: AsyncIterable[str | bytes], group: asyncio.TaskGroup
    ) -> None:
        """Subscribe on the first frame, then dispatch every later one by type."""
        stream = aiter(inbound)
        try:
            async with asyncio.timeout(frames.SUB_TIMEOUT):
                first = await anext(stream, None)
        except TimeoutError:
            await self._fail(Close.PROTOCOL)
        if first is None:
            raise _ClosedError(None)
        frame = await self._parse(first)
        if not isinstance(frame, Subscribe):
            await self._fail(Close.PROTOCOL)
        group.create_task(self._deliver(frame))
        async for item in stream:
            await self._dispatch(await self._parse(item))
        raise _ClosedError(None)

    async def _parse(self, item: str | bytes) -> frames.Inbound:
        try:
            if isinstance(item, str):
                return frames.parse(item)
            return frames.parse(None, item)
        except FrameError as exc:
            await self._fail(exc.code)

    async def _dispatch(self, frame: frames.Inbound) -> None:
        """Route one frame after the subscription, answering unknown ones in place."""
        match frame:
            case Pong():
                return
            case _:
                await self._writer.send(frames.error("unsupported"))

    async def _deliver(self, frame: Subscribe) -> None:
        """Stream the granted topics in the fixed subscribe, catch up, hello order."""
        claims = await self._subscribe(frame)
        await self._grant()
        broker = self._broker
        if broker is None:
            broker = self._broker = await broker_manager.aget()
        async with broker.asubscribe(
            claims.topics, timeout=self._heartbeat
        ) as subscription:
            positions = await broker.apositions(claims.topics)
            await self.catch_up(subscription, positions)
            await self._writer.send(
                frames.hello(
                    claims.topics,
                    positions,
                    heartbeat=self._heartbeat,
                    server_time=self._now(),
                )
            )
            async for item in subscription:
                await self._grant()
                if item is None:
                    await self.tick()
                else:
                    await self._writer.send(await self._frame_for(item))

    async def _subscribe(self, frame: Subscribe) -> TicketClaims:
        """Verify the ticket for the handshake session and build its access check."""
        try:
            claims = verify_ticket(frame.ticket, self.handshake.session_key)
            transform, authorizer = resolve_claims(claims)
        except InvalidTicketError:
            await self._fail(Close.SESSION)
        self.cursor = frame.cursor
        self._transform = transform
        self._access = AccessCheck(
            claims.topics,
            authorizer=authorizer,
            session_key=self.handshake.session_key,
            user=self.handshake.user,
            interval=self._heartbeat,
            expires_at=self.handshake.expires_at,
        )
        self._access.bind()
        return claims

    async def _grant(self) -> None:
        verdict = await self._access.acheck()
        if verdict is not Access.GRANTED:
            await self._fail(_CLOSE_CODES[verdict])

    async def _frame_for(self, message: Message) -> str:
        """Encode one message for this subscriber, transformed off the event loop."""
        if self._transform is not None:
            result = transformed(message, await run_detached(self._transform, message))
            if result is None:
                return frames.skip(message)
            message = result
        return frames.patches(
            message, partial_backend_manager.get(), server_time=self._now()
        )

    async def _watch(self) -> None:
        """Drop a connection whose peer stopped reading for a whole heartbeat."""
        while True:
            await asyncio.sleep(self._heartbeat)
            if self._writer.stalled(self._heartbeat):
                logger.info("Realtime WebSocket dropped a client that stopped reading")
                raise _ClosedError(None)

    async def _fail(self, code: Close) -> NoReturn:
        """Send the error frame a close code carries, then close with it."""
        reason = _REASONS.get(code)
        if reason is not None:
            await self._writer.send(frames.error(reason))
        raise _ClosedError(code)

    def _now(self) -> int:
        return int(self._clock() * 1000)


__all__ = ["Handshake", "SocketPort", "TopicSocket"]
