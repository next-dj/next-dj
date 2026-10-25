import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from functools import partial

import fakeredis
import fakeredis._basefakesocket
import fakeredis._commands
import fakeredis.aioredis
import pytest
from fakeredis.commands_mixins import server_mixin

from next.partial import Envelope, Patches
from next.realtime.backends import BrokerBackend, MemoryBroker
from next.realtime.redis import RedisBroker
from next.realtime.roster import PresenceSchedule, Scheduler
from next.testing.realtime import SocketTicket, issue_socket_ticket


ROOM_AUTHORIZER_REF = "tests.support.realtime.allow_room_topic"


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


def issue_session_ticket(
    topics: tuple[str, ...] = ("room:42",),
    *,
    transform_ref: str | None = None,
    authorizer_ref: str = ROOM_AUTHORIZER_REF,
    presence: bool | str = False,
) -> SocketTicket:
    """Issue a ticket for the ``room:`` topics the realtime tests subscribe to."""
    return issue_socket_ticket(
        topics,
        transform_ref=transform_ref,
        authorizer_ref=authorizer_ref,
        presence=presence,
    )


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


HEARTBEAT_FRAME = b": heartbeat\n\n"


def next_frame(iterator: Iterator[bytes]) -> bytes:
    """Return the next SSE chunk that is not a heartbeat, whatever the timing."""
    frame = next(iterator)
    while frame == HEARTBEAT_FRAME:
        frame = next(iterator)
    return frame


async def anext_frame(iterator: AsyncIterator[bytes]) -> bytes:
    """Return the next async SSE chunk that is not a heartbeat."""
    frame = await anext(iterator)
    while frame == HEARTBEAT_FRAME:
        frame = await anext(iterator)
    return frame


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

    def sse(self, frame: bytes) -> str:
        """Feed one SSE frame, checking its id matches the payload cursor."""
        lines = frame.decode().strip().split("\n")
        fields = dict(line.split(": ", 1) for line in lines)
        data = json.loads(fields["data"])
        if fields["event"] == "next-hello":
            ((epoch, seq),) = (
                (position["epoch"], position["seq"])
                for position in data["topics"].values()
            )
            assert fields["id"] == f"{epoch}:{seq}"
            self.hello(data)
        else:
            assert fields["id"] == f"{data['epoch']}:{data['seq']}"
            self.patches(data)
        return fields["event"]
