"""The realtime topic protocol over any WebSocket, spoken through a two-method port."""

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, NoReturn, Protocol, Self

from next.partial.manager import partial_backend_manager

from . import frames
from .access import (
    Access,
    AccessCheck,
    SocketAccessCheck,
    heartbeat_seconds,
    run_detached,
)
from .attendance import Attendance, aattend, aattending
from .backends import BrokerBackend
from .binding import opaque_subject, ticket_binding
from .delivery import transformed
from .errors import BrokerConnectionError, InvalidTicketError
from .frames import (
    Close,
    FrameError,
    Pong,
    Refusal,
    Reply,
    Request,
    Subscribe,
    Up,
    UpstreamError,
)
from .handlers import Upstream, dispatch as dispatch_upstream
from .limits import SocketLimits, TokenBucket, TunnelLimits
from .manager import broker_manager
from .messages import Message, Position
from .replay import acatch_up
from .roster import Presence, counted, snapshot_message
from .signals import StreamLifecycle, UnsubscribeReason
from .tickets import TicketClaims, resolve_claims, resolve_identify, verify_ticket
from .tunnel import Tunnel


if TYPE_CHECKING:
    from .access import TopicAuthorizer
    from .attendance import Identify
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
    Close.RATE_LIMITED: "limit",
}
_LEFT: dict[int | None, UnsubscribeReason] = {
    None: UnsubscribeReason.CLIENT,
    Close.SESSION: UnsubscribeReason.REVOKED,
    Close.TOPIC_DENIED: UnsubscribeReason.REVOKED,
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
    """The credential a WebSocket was accepted for, the one its ticket was issued under.

    `subject` is the opaque id handlers see, `credential` stays with the rechecks.
    """

    credential: str = field(repr=False)
    user: object
    expires_at: float | None
    subject: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive the opaque subject once, so no handler needs the credential."""
        object.__setattr__(self, "subject", opaque_subject(self.credential))

    @classmethod
    async def from_cookies(cls, cookies: Mapping[str, str]) -> Self | None:
        """Load the ticket binding's credential and live user, or None without one.

        Both reads take one trip to the shared pool, none without a cookie at all.
        """
        if not cookies:
            return None
        return await run_detached(cls._from_cookies, cookies)

    @classmethod
    def _from_cookies(cls, cookies: Mapping[str, str]) -> Self | None:
        binding = ticket_binding()
        credential = binding.credential(cookies)
        if credential is None:
            return None
        live = binding.live(credential)
        if live is None:
            return None
        user, left = live
        expires_at = None if left is None else time.monotonic() + left
        return cls(credential, user, expires_at)


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

    strikes: ClassVar[int] = 3
    _access: AccessCheck

    def __init__(
        self,
        port: SocketPort,
        handshake: Handshake,
        *,
        broker: BrokerBackend | None = None,
        heartbeat: float | None = None,
        clock: Callable[[], float] = time.time,
        tunnel: Tunnel | None = None,
        limits: SocketLimits | None = None,
    ) -> None:
        """Bind the connection to its port and session without touching the network.

        Without `limits` the settings are read here, `WebSocketApp` reads them once.
        """
        self.handshake = handshake
        self.connection_id = secrets.token_urlsafe(16)
        self.cursor: Mapping[str, Position | int] = MappingProxyType({})
        self._writer = _Writer(port)
        self._broker = broker
        self._heartbeat = heartbeat if heartbeat is not None else heartbeat_seconds()
        self._clock = clock
        self._transform: TopicTransform | None = None
        self._identify: Identify | None = None
        self._limits = limits if limits is not None else SocketLimits.configured()
        self._tunnel = tunnel if self._limits.tunnel is not None else None
        upstream = self._limits.upstream
        self._bucket = TokenBucket(upstream.rate, upstream.burst, clock=clock)
        self._strikes = 0
        self._events = 0
        self._requests = 0
        self._topics: frozenset[str] = frozenset()
        self._ready = asyncio.Event()
        self._lifecycle = StreamLifecycle(self, "ws")

    async def serve(self, inbound: AsyncIterable[str | bytes]) -> None:
        """Run the connection until the client leaves or the protocol closes it.

        The presence a ticket asks for is left on every way out, a cancellation too.
        """
        reason = UnsubscribeReason.CANCELLED
        try:
            reason = await self._run(inbound)
        finally:
            await self._lifecycle.aclose(reason)

    async def _run(self, inbound: AsyncIterable[str | bytes]) -> UnsubscribeReason:
        """Serve the connection and return why its subscription, if any, ended."""
        codes: list[int | None] = []
        reasons: list[UnsubscribeReason] = []
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
            reasons.extend(_LEFT.get(code, UnsubscribeReason.SERVER) for code in codes)
        except* BrokerConnectionError:
            logger.info("Realtime WebSocket stream lost its broker subscription")
            codes.append(Close.INTERNAL)
            reasons.append(UnsubscribeReason.ERROR)
        except* OSError:
            logger.warning("Realtime WebSocket broker connection failed", exc_info=True)
            codes.append(Close.INTERNAL)
            reasons.append(UnsubscribeReason.ERROR)
        except* Exception:
            logger.exception("Realtime WebSocket stream failed")
            codes.append(Close.INTERNAL)
            reasons.append(UnsubscribeReason.ERROR)
        code = codes[0] if codes else None
        if code is not None and None not in codes:
            await self._writer.close(code)
        return reasons[0] if reasons else UnsubscribeReason.SERVER

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
            await self._dispatch(await self._parse(item), group)
        raise _ClosedError(None)

    async def _parse(self, item: str | bytes) -> frames.Inbound:
        try:
            if isinstance(item, str):
                return frames.parse(item)
            return frames.parse(None, item)
        except FrameError as exc:
            await self._fail(exc.code)

    async def _dispatch(self, frame: frames.Inbound, group: asyncio.TaskGroup) -> None:
        """Route one frame after the subscription, each one drawing a token first.

        An event or a request runs as its own task, so a slow one never stalls a pong.
        """
        admitted = await self._admit()
        match frame:
            case Pong():
                return
            case Up():
                if admitted:
                    group.create_task(self._up(frame))
                else:
                    await self._refuse_up(frame.id)
            case Request():
                if admitted:
                    group.create_task(self._request(frame))
                else:
                    await self._writer.send(frames.reply(frame.id, Refusal.LIMIT))
            case _:
                if admitted:
                    await self._writer.send(frames.error("unsupported"))

    async def _admit(self) -> bool:
        """Draw a token for a frame, closing with 4429 after `strikes` refusals."""
        if self._bucket.take():
            self._strikes = 0
            return True
        self._strikes += 1
        if self._strikes >= self.strikes:
            await self._fail(Close.RATE_LIMITED)
        return False

    async def _refuse_up(self, frame_id: str | None) -> None:
        if frame_id is not None:
            await self._writer.send(frames.ack(frame_id, UpstreamError.LIMIT))
        else:
            await self._writer.send(frames.error("limit"))

    async def _up(self, frame: Up) -> None:
        """Hand an event to its handler within the connection's concurrency, then ack.

        An event past `UPSTREAM['CONCURRENCY']` is refused but never counts to 4429.
        """
        if self._events >= self._limits.upstream.concurrency:
            await self._refuse_up(frame.id)
            return
        self._events += 1
        try:
            error = await self._deliver_up(frame)
            if frame.id is not None:
                await self._writer.send(frames.ack(frame.id, error))
        finally:
            self._events -= 1

    async def _deliver_up(self, frame: Up) -> UpstreamError | None:
        if frame.size > self._limits.upstream.max_bytes:
            return UpstreamError.TOO_BIG
        await self._ready.wait()
        if frame.topic not in self._topics:
            return UpstreamError.TOPIC
        verdict = await self._access.acheck()
        if verdict is not Access.GRANTED:
            if frame.id is not None:
                await self._writer.send(frames.ack(frame.id, UpstreamError.DENIED))
            await self._fail(_CLOSE_CODES[verdict])
        upstream = Upstream(
            frame.topic,
            frame.name,
            frame.data,
            self.handshake.user,
            self.handshake.subject,
            self.connection_id,
        )
        try:
            async with asyncio.timeout(self._limits.upstream.timeout):
                delivered = await self.handle(upstream)
        except TimeoutError:
            logger.warning("Realtime upstream handler for %r timed out", frame.name)
            return UpstreamError.TIMEOUT
        except Exception:
            logger.exception("Realtime upstream handler for %r failed", frame.name)
            return UpstreamError.INTERNAL
        return None if delivered else UpstreamError.UNKNOWN

    async def handle(self, upstream: Upstream) -> bool:
        """Route one granted event, to its `@realtime.on` handler by default.

        False means nothing serves the event, and the client gets `unknown`.
        """
        return await dispatch_upstream(upstream)

    async def _request(self, frame: Request) -> None:
        """Run a tunneled request within the connection's concurrency and timeout.

        The access recheck runs first, as for an event, so a revoked grant closes.
        """
        limits, tunnel = self._limits.tunnel, self._tunnel
        if limits is None or tunnel is None:
            await self._writer.send(frames.reply(frame.id, Refusal.DISABLED))
            return
        if self._requests >= limits.concurrency:
            await self._writer.send(frames.reply(frame.id, Refusal.LIMIT))
            return
        self._requests += 1
        try:
            await self._ready.wait()
            await self._grant()
            answer = await self._tunneled(tunnel, frame, limits)
            await self._writer.send(frames.reply(frame.id, answer))
        finally:
            self._requests -= 1

    async def _tunneled(
        self, tunnel: Tunnel, frame: Request, limits: TunnelLimits
    ) -> Reply | Refusal:
        try:
            async with asyncio.timeout(limits.timeout):
                return await tunnel(frame, connection=self.connection_id)
        except TimeoutError:
            logger.warning("Realtime tunnel request to %s timed out", frame.path)
            return Refusal.TIMEOUT
        except Exception:
            logger.exception("Realtime tunnel request to %s failed", frame.path)
            return Refusal.INTERNAL

    async def _deliver(self, frame: Subscribe) -> None:
        """Stream the granted topics in the subscribe, join, catch up, hello order.

        The join follows the positions, so its snapshot lands past the hello cursor.
        """
        claims = await self._subscribe(frame)
        await self._grant()
        self._ready.set()
        broker = self._broker
        if broker is None:
            broker = self._broker = await broker_manager.aget()
        async with broker.asubscribe(
            claims.topics, timeout=self._heartbeat
        ) as subscription:
            await self._lifecycle.aopen(
                claims.topics, subject=self.handshake.subject, user=self.handshake.user
            )
            positions = await broker.apositions(claims.topics)
            attendance = await self.attend(broker, claims)
            async with aattending(attendance, subscription) as stream:
                await self.catch_up(subscription, positions)
                await self._writer.send(
                    frames.hello(
                        claims.topics,
                        positions,
                        heartbeat=self._heartbeat,
                        server_time=self._now(),
                        limits=self._hello_limits(),
                    )
                )
                if attendance is not None:
                    await self._snapshots(attendance, claims.topics)
                async for item in stream:
                    await self._grant()
                    if item is None:
                        await self.tick()
                    else:
                        await self._writer.send(await self._frame_for(item))
        await self._lifecycle.aclose(UnsubscribeReason.SERVER)

    def _hello_limits(self) -> dict[str, object]:
        """Return the frame limits hello tells the client, the tunnel only when on."""
        limits: dict[str, object] = {
            "upstream": {"frame": self._limits.upstream.max_bytes}
        }
        tunnel, settings = self._tunnel, self._limits.tunnel
        if tunnel is not None and settings is not None:
            hints = tunnel.hints()
            limits["tunnel"] = {
                "timeout": settings.timeout,
                "frame": frames.MAX_INBOUND_BYTES,
                "headers": sorted(hints.headers),
                "prefixes": list(hints.prefixes),
                "csrf": hints.csrf,
            }
        return limits

    async def attend(
        self, broker: BrokerBackend, claims: TicketClaims
    ) -> Attendance | None:
        """Return the attendance the ticket asks for on its topics, None without.

        An override returns another `Attendance`, a subclass with its own TTL too.
        """
        if claims.presence is False:
            return None
        return await aattend(
            broker,
            claims.topics,
            self._identify,
            self.handshake.user,
            heartbeat=self._heartbeat,
            connection=self.connection_id,
        )

    async def _snapshots(self, attendance: Attendance, topics: tuple[str, ...]) -> None:
        """Send each topic's current presence right after hello, unnumbered."""
        for topic in topics:
            snapshot = await attendance.broker.apresence(topic)
            if self._identify is None:
                snapshot = Presence(snapshot.count)
            message = snapshot_message(topic, snapshot, self._clock())
            await self._writer.send(await self._frame_for(message))

    async def _subscribe(self, frame: Subscribe) -> TicketClaims:
        """Verify the ticket for the handshake session and build its access check."""
        try:
            claims = verify_ticket(frame.ticket, self.handshake.credential)
            transform, authorizer = resolve_claims(claims)
            self._identify = resolve_identify(claims)
        except InvalidTicketError:
            await self._fail(Close.SESSION)
        self.cursor = frame.cursor
        self._transform = transform
        self._access = self.access_check(claims, authorizer)
        self._access.bind()
        self._topics = frozenset(claims.topics)
        return claims

    def access_check(
        self, claims: TicketClaims, authorizer: "TopicAuthorizer | None"
    ) -> AccessCheck:
        """Build the throttled recheck of the ticket, once per heartbeat by default.

        An override may recheck more often, before every `up` event too.
        """
        return SocketAccessCheck(
            claims.topics,
            authorizer=authorizer,
            session_key=self.handshake.credential,
            user=self.handshake.user,
            interval=self._heartbeat,
            expires_at=self.handshake.expires_at,
        )

    async def _grant(self) -> None:
        verdict = await self._access.acheck()
        if verdict is not Access.GRANTED:
            await self._fail(_CLOSE_CODES[verdict])

    async def _frame_for(self, message: Message) -> str:
        """Encode one message for this subscriber, transformed off the event loop.

        A subscriber outside identify mode gets presence events without their members.
        """
        if message.seq and self._transform is not None:
            result = transformed(message, await run_detached(self._transform, message))
            if result is None:
                return frames.skip(message)
            message = result
        if self._identify is None:
            message = counted(message)
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
