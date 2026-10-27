import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from functools import partial

import fakeredis
import fakeredis._basefakesocket
import fakeredis._commands
import fakeredis.aioredis
import pytest
from django.contrib.auth import get_user_model, login
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import Permission
from django.contrib.sessions.backends.base import SessionBase
from django.test import AsyncRequestFactory, RequestFactory
from django.utils.module_loading import import_string
from fakeredis.commands_mixins import server_mixin

from next.partial import Envelope, Patches
from next.realtime import TopicStream, WebSocketApp
from next.realtime.backends import BrokerBackend, MemoryBroker
from next.realtime.binding import session_store_class
from next.realtime.messages import Message, Position
from next.realtime.redis import RedisBroker
from next.realtime.roster import PresenceSchedule, Scheduler
from next.realtime.signals import stream_subscribed, stream_unsubscribed
from next.testing import SignalRecorder
from next.testing.realtime import (
    SocketTicket,
    SseEvent,
    SseReader,
    WebSocketCommunicator,
    issue_socket_ticket,
)


ROOM_AUTHORIZER_REF = "tests.support.realtime.allow_room_topic"
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def allow_room_topic(_user: object, topic: str) -> bool:
    """Grant every topic under the ``room:`` prefix and nothing else."""
    return topic.startswith("room:")


def manual_schedule(clock: Callable[[], float]) -> Scheduler:
    """Build presence schedules a test runs by hand on its own clock."""
    return partial(PresenceSchedule, clock=clock, threaded=False)


def silent_schedule(announce: Callable[[str], object]) -> PresenceSchedule:
    """Build a presence schedule that queues windows and never announces them."""
    del announce
    return PresenceSchedule(_unannounced, threaded=False)


def _unannounced(_topic: str) -> None:
    """Drop a closed window without a snapshot."""


def signed_in_session(
    username: str = "reader", *perms: str
) -> tuple[AbstractBaseUser, SessionBase]:
    """Create a user holding the `perms` codenames and a saved session signed in."""
    user = get_user_model().objects.create_user(username=username)
    if perms:
        user.user_permissions.add(*Permission.objects.filter(codename__in=perms))
    request = RequestFactory().get("/")
    request.session = session_store_class()()
    login(request, user, backend=MODEL_BACKEND)
    request.session.save()
    return user, request.session


def issue_session_ticket(
    topics: tuple[str, ...] = ("room:42",),
    *,
    transform_ref: str | None = None,
    authorizer_ref: str = ROOM_AUTHORIZER_REF,
    presence: bool | str = False,
    session: SessionBase | None = None,
) -> SocketTicket:
    """Issue a ticket for the ``room:`` topics the realtime tests subscribe to."""
    return issue_socket_ticket(
        topics,
        transform_ref=transform_ref,
        authorizer_ref=authorizer_ref,
        presence=presence,
        session=session,
    )


def websocket_scope(
    headers: Sequence[tuple[bytes, bytes]] = (), **overrides: object
) -> dict[str, object]:
    """Build the ASGI scope of a WebSocket handshake on the framework mount."""
    scope: dict[str, object] = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": "/_next/ws/",
        "raw_path": b"/_next/ws/",
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": None,
        "server": ("testserver", 80),
        "subprotocols": [],
        "extensions": {},
    }
    scope.update(overrides)
    return scope


class LiveStreams(SignalRecorder):
    """Count the streams that announced a subscription and have not left it yet."""

    def __init__(self) -> None:
        """Record both stream signals."""
        super().__init__(stream_subscribed, stream_unsubscribed)

    def live(self, topic: str | None = None) -> int:
        """Return how many streams, on `topic` when given, are still subscribed."""
        count = 0
        for event in self.events:
            if topic is None or topic in event.kwargs["topics"]:
                count += 1 if event.signal is stream_subscribed else -1
        return count


def refresh_envelope() -> Envelope:
    """Build the topic-safe refresh envelope most broker tests publish."""
    return Patches(None).refresh(zone="results").envelope()


def message_payload(**overrides: object) -> dict[str, object]:
    """Build the JSON-safe mapping of one broker message with chosen fields replaced."""
    payload: dict[str, object] = {
        "topic": "poll:1",
        "seq": 3,
        "epoch": "e1",
        "envelope": refresh_envelope().as_dict(),
        "published_at": 1.5,
    }
    payload.update(overrides)
    return payload


async def anext_item[T](source: AsyncIterator[T | None]) -> T:
    """Return the next item of a topic source, skipping its heartbeat `None`."""
    item = await anext(source)
    while item is None:
        item = await anext(source)
    return item


class PlainBroker(BrokerBackend):
    """Delegate to a memory broker without exposing its replay methods."""

    def __init__(self) -> None:
        """Wrap a fresh memory broker."""
        super().__init__()
        self.inner = MemoryBroker()

    def publish(self, topic, envelope):
        return self.inner.publish(topic, envelope)

    async def apublish(self, topic, envelope):
        return self.inner.publish(topic, envelope)

    def subscribe(self, topics, *, timeout):
        return self.inner.subscribe(topics, timeout=timeout)

    def asubscribe(self, topics, *, timeout):
        return self.inner.asubscribe(topics, timeout=timeout)

    def positions(self, topics):
        return self.inner.positions(topics)

    async def apositions(self, topics):
        return self.inner.positions(topics)


class ScriptedBroker(MemoryBroker):
    """A memory broker whose subscription yields what the test feeds it."""

    def __init__(
        self,
        calls: list[str] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        schedule: Scheduler | None = None,
    ) -> None:
        """Start with an empty feed and an optional shared call log."""
        super().__init__(clock=clock, schedule=schedule)
        self.calls = calls if calls is not None else []
        self.timeouts: list[float] = []
        self.feed: asyncio.Queue[Message | None] = asyncio.Queue()

    def asubscribe(self, topics: Sequence[str], *, timeout: float):
        self.calls.append("asubscribe")
        self.timeouts.append(timeout)
        selected = tuple(topics)
        subscriber = object()

        @asynccontextmanager
        async def subscription() -> AsyncIterator[AsyncIterator[Message | None]]:
            self._register(selected, subscriber)
            try:
                yield self._fed()
            finally:
                self._unregister(selected, subscriber)

        return subscription()

    async def _fed(self) -> AsyncIterator[Message | None]:
        while True:
            yield await self.feed.get()

    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        self.calls.append("apositions")
        return await super().apositions(topics)


class FakeServerBroker(RedisBroker):
    """Reach one fakeredis server from the sync client and every per-loop client."""

    def __init__(self, server: fakeredis.FakeServer, **kwargs: object) -> None:
        """Keep the server before the base class builds the sync client."""
        self.server = server
        super().__init__(**kwargs)

    def create_client(self, url, options, *, asynchronous):
        factory = fakeredis.aioredis.FakeRedis if asynchronous else fakeredis.FakeRedis
        return factory(server=self.server, decode_responses=True)


class ServerClock:
    """A wall clock a test moves by hand, for brokers and the fakeredis server alike."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        """Start at a fixed Unix time."""
        self.now = now

    def __call__(self) -> float:
        return self.now

    def time(self) -> float:
        """Answer as `time.time` does for the fakeredis modules that read it."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


def freeze_fakeredis(monkeypatch: pytest.MonkeyPatch, clock: ServerClock) -> None:
    """Make fakeredis read `clock` for `TIME`, key expiry and timestamp arguments."""
    for module in (server_mixin, fakeredis._basefakesocket, fakeredis._commands):
        monkeypatch.setattr(module, "time", clock)


ROOM_TOPIC = "room:42"


def published(broker: BrokerBackend, count: int, topic: str = ROOM_TOPIC) -> str:
    """Publish `count` refresh envelopes on `topic` and return their epoch."""
    messages = [broker.publish(topic, refresh_envelope()) for _ in range(count)]
    return messages[0].epoch


def publish_during(
    broker: BrokerBackend,
    step: str,
    monkeypatch: pytest.MonkeyPatch,
    topic: str = ROOM_TOPIC,
) -> None:
    """Publish one live message the first time a stream reaches a broker step."""
    fired: list[bool] = []
    sync_step = getattr(broker, step)
    async_step = getattr(broker, f"a{step}")

    def fire() -> None:
        if not fired:
            fired.append(True)
            broker.publish(topic, refresh_envelope())

    def publishing(*args: object) -> object:
        fire()
        return sync_step(*args)

    async def apublishing(*args: object) -> object:
        fire()
        return await async_step(*args)

    monkeypatch.setattr(broker, step, publishing)
    monkeypatch.setattr(broker, f"a{step}", apublishing)


@dataclass(frozen=True, slots=True)
class WireFrame:
    """One frame a client reads, in the same shape whichever transport carried it."""

    kind: str
    topic: str = ""
    epoch: str = ""
    seq: int = 0
    code: str = ""
    data: Mapping[str, object] = field(default_factory=dict)

    @property
    def trace(self) -> object:
        """Name the frame by its seq, or by its kind and seq, so an order reads fast."""
        if self.kind == "patches":
            return self.seq
        if self.kind == "end":
            return ("end", self.code)
        return (self.kind, self.seq)


def _positioned(kind: str, topics: object) -> WireFrame:
    """Read the single topic position of a hello."""
    assert isinstance(topics, Mapping)
    ((topic, position),) = topics.items()
    return WireFrame(kind, topic, str(position["epoch"]), int(position["seq"]))


def _numbered(kind: str, data: Mapping[str, object]) -> WireFrame:
    """Read the topic cursor of a patches or skip payload."""
    topic, epoch = str(data["topic"]), str(data.get("epoch", ""))
    return WireFrame(kind, topic, epoch, int(str(data["seq"])), data=data)


def sse_frame(event: SseEvent) -> WireFrame:
    """Read one SSE event as a wire frame."""
    data = event.json()
    kind = event.event.removeprefix("next-")
    if kind == "hello":
        return _positioned(kind, data["topics"])
    if kind == "end":
        return WireFrame(kind, code=str(data["code"]))
    assert kind in {"patches", "skip"}, event
    return _numbered(kind, data)


def socket_frame(frame: Mapping[str, object]) -> WireFrame:
    """Read one WebSocket frame as a wire frame."""
    kind = str(frame["type"])
    if kind == "hello":
        return _positioned(kind, frame["topics"])
    if kind == "error":
        return WireFrame("end", code=str(frame["code"]))
    if kind == "patches":
        data = frame["data"]
        assert isinstance(data, Mapping)
        return _numbered(kind, data)
    assert kind == "skip", frame
    return _numbered(kind, frame)


class Wire:
    """A client of one topic that reads its frames off SSE or the WebSocket."""

    def __init__(
        self,
        receive: Callable[[], Awaitable[WireFrame]],
        end: Callable[[], Awaitable[int | None]],
    ) -> None:
        """Read through `receive` and wait for the server to hang up through `end`."""
        self._receive = receive
        self._end = end

    async def receive(self) -> WireFrame:
        """Return the next frame, failing a test that waits too long."""
        async with asyncio.timeout(5):
            return await self._receive()

    async def frames(self, count: int) -> list[WireFrame]:
        """Return the next `count` frames."""
        return [await self.receive() for _ in range(count)]

    async def ends(self) -> int | None:
        """Assert the server ends the connection, returning a WebSocket close code."""
        async with asyncio.timeout(5):
            return await self._end()


@dataclass(frozen=True, slots=True)
class WireOpener:
    """Open a `Wire` on one transport, `sse-sync`, `sse-async` or `ws`."""

    transport: str

    def __call__(
        self,
        topic: str = ROOM_TOPIC,
        *,
        cursor: Position | None = None,
        authorizer_ref: str = ROOM_AUTHORIZER_REF,
        transform_ref: str | None = None,
    ) -> AbstractAsyncContextManager[Wire]:
        """Prepare the connection now, so a WebSocket ticket is issued off the loop."""
        if self.transport == "ws":
            ticket = issue_session_ticket(
                (topic,), authorizer_ref=authorizer_ref, transform_ref=transform_ref
            )
            last_seq = None if cursor is None else {topic: cursor.as_dict()}
            return _over_websocket(ticket, last_seq)
        asynchronous = self.transport == "sse-async"
        factory = AsyncRequestFactory() if asynchronous else RequestFactory()
        headers = {} if cursor is None else {"Last-Event-ID": cursor.cursor}
        stream = TopicStream(
            factory.get("/rooms/42/stream/", headers=headers),
            topic,
            authorize=import_string(authorizer_ref),
            transform=None if transform_ref is None else import_string(transform_ref),
            heartbeat_seconds=15,
        )
        return _over_sse(SseReader(stream), asynchronous=asynchronous)


@asynccontextmanager
async def _over_sse(events: SseReader, *, asynchronous: bool) -> AsyncIterator[Wire]:
    """Read an SSE response, a sync one inline since its frames are already queued."""
    if asynchronous:

        async def receive() -> WireFrame:
            return sse_frame(await anext(events))

        async def end() -> None:
            with pytest.raises(StopAsyncIteration):
                await anext(events)

        async with events:
            yield Wire(receive, end)
        return

    async def pull() -> WireFrame:
        return sse_frame(next(events))

    async def stop() -> None:
        with pytest.raises(StopIteration):
            next(events)

    with events:
        yield Wire(pull, stop)


@asynccontextmanager
async def _over_websocket(
    ticket: SocketTicket, last_seq: Mapping[str, object] | None
) -> AsyncIterator[Wire]:
    """Subscribe through the WebSocket app with the ticket and an optional cursor."""
    communicator = WebSocketCommunicator(
        WebSocketApp(),
        "/_next/ws/",
        headers=[
            (b"host", b"testserver"),
            (b"origin", b"http://testserver"),
            ticket.cookie,
        ],
        timeout=5,
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.send_json_to(ticket.subscribe_frame(last_seq))

    async def receive() -> WireFrame:
        return socket_frame(await communicator.receive_json_from())

    try:
        yield Wire(receive, communicator.receive_close)
    finally:
        await communicator.disconnect()


class ResumingClient:
    """Mirror the client runtime cursor rules that drop duplicates and spot gaps."""

    def __init__(self, cursors: Mapping[str, tuple[str, int]]) -> None:
        """Start from the cursors a reconnecting client already holds."""
        self.cursors = dict(cursors)
        self.applied: list[tuple[str, str, int]] = []
        self.dropped: list[tuple[str, str, int]] = []
        self.gaps: list[tuple[str, int, int]] = []

    def patches(self, data: Mapping[str, object]) -> None:
        """Apply a patches frame unless its (epoch, seq) is not past the cursor."""
        topic, epoch, seq = str(data["topic"]), str(data["epoch"]), int(data["seq"])
        previous = self.cursors.get(topic)
        if previous is not None and previous[0] == epoch and seq <= previous[1]:
            self.dropped.append((topic, epoch, seq))
            return
        self.cursors[topic] = (epoch, seq)
        self.applied.append((topic, epoch, seq))
        if previous is not None and (
            seq > previous[1] + 1 or previous[0] not in {epoch, ""}
        ):
            self.gaps.append((topic, previous[1], seq))

    def hello(self, data: Mapping[str, object]) -> None:
        """Adopt the hello positions and record a gap wherever they differ."""
        topics = data["topics"]
        assert isinstance(topics, Mapping)
        for topic, position in topics.items():
            epoch, seq = str(position["epoch"]), int(position["seq"])
            previous = self.cursors.get(topic)
            if previous == (epoch, seq):
                continue
            self.cursors[topic] = (epoch, seq)
            if previous is not None or seq > 0:
                self.gaps.append((topic, 0 if previous is None else previous[1], seq))

    def read(self, frame: WireFrame) -> None:
        """Feed one wire frame, a hello or a numbered patches frame."""
        cursor = {"topic": frame.topic, "epoch": frame.epoch, "seq": frame.seq}
        if frame.kind == "hello":
            self.hello({"topics": {frame.topic: cursor}})
        else:
            self.patches(cursor)

    def sse(self, event: SseEvent) -> str:
        """Feed one SSE event, checking its id matches the payload cursor."""
        data = event.json()
        if event.event == "next-hello":
            topics = data["topics"]
            assert isinstance(topics, Mapping)
            ((epoch, seq),) = (
                (position["epoch"], position["seq"]) for position in topics.values()
            )
            assert event.id == f"{epoch}:{seq}"
            self.hello(data)
        else:
            assert event.id == f"{data['epoch']}:{data['seq']}"
            self.patches(data)
        return event.event
