import asyncio
from contextlib import AbstractAsyncContextManager
from functools import partial

import pytest

from next.partial import Patches
from next.realtime import (
    BrokerBackend,
    Position,
    apublish,
    socket as socket_module,
    topics as topics_module,
)
from next.realtime.access import Access, AccessCheck, SocketAccessCheck
from next.realtime.frames import Close
from next.realtime.manager import broker_manager
from next.realtime.messages import Message
from tests.support.realtime import (
    ROOM_TOPIC,
    PlainBroker,
    ResumingClient,
    ServerClock,
    Wire,
    WireFrame,
    WireOpener,
    publish_during,
    published,
    refresh_envelope,
)


_HERE = "tests.realtime.test_parity"
_GRANTS = {"room": True}


def toggled_authorizer(_user: object, topic: str) -> bool:
    return _GRANTS["room"] and topic.startswith("room:")


def skip_everything(_message: Message) -> None:
    return None


def skip_second(message: Message) -> Patches | None:
    if message.seq == 2:
        return None
    return Patches(None).event("room:update", {"seq": message.seq})


def fail_even(message: Message) -> Patches:
    if message.seq % 2 == 0:
        msg = "transform failed"
        raise RuntimeError(msg)
    return Patches(None).event("room:update", {"seq": message.seq})


def unsafe_payload(_message: Message) -> str:
    return "<p>not an envelope</p>"


@pytest.fixture(autouse=True)
def _grant_the_room() -> None:
    _GRANTS["room"] = True


@pytest.fixture()
def access_clock(monkeypatch: pytest.MonkeyPatch, clock: ServerClock) -> ServerClock:
    monkeypatch.setattr(
        socket_module, "SocketAccessCheck", partial(SocketAccessCheck, clock=clock)
    )
    monkeypatch.setattr(topics_module, "AccessCheck", partial(AccessCheck, clock=clock))
    return clock


@pytest.mark.usefixtures("memory_broker")
def test_revoked_grant_says_hello_skip_and_end(
    wire: WireOpener, access_clock: ServerClock
) -> None:
    opening = wire(
        authorizer_ref=f"{_HERE}.toggled_authorizer",
        transform_ref=f"{_HERE}.skip_everything",
    )

    async def scenario() -> list[object]:
        async with opening as client:
            hello = await client.receive()
            await apublish(ROOM_TOPIC, refresh_envelope())
            skipped = await client.receive()
            _GRANTS["room"] = False
            access_clock.advance(10_000)
            await apublish(ROOM_TOPIC, refresh_envelope())
            ended = await client.receive()
            closed = await client.ends()
        assert (hello.topic, hello.epoch) == (ROOM_TOPIC, "")
        return [hello.trace, skipped.trace, ended.trace, closed]

    closed = Close.TOPIC_DENIED if wire.transport == "ws" else None
    traced = [("hello", 0), ("skip", 1), ("end", "auth"), closed]
    assert asyncio.run(scenario()) == traced


def _resumed(
    opening: AbstractAsyncContextManager[Wire],
    broker: BrokerBackend,
    count: int,
    *,
    live: int = 0,
) -> list[WireFrame]:
    """Read `count` frames, publish `live` more and read those too."""

    async def scenario() -> list[WireFrame]:
        async with opening as client:
            frames = await client.frames(count)
            for _ in range(live):
                await broker.apublish(ROOM_TOPIC, refresh_envelope())
            return frames + await client.frames(live)

    return asyncio.run(scenario())


def test_cursor_in_the_current_epoch_replays_the_gap_before_hello(
    wire: WireOpener, replaying_broker: BrokerBackend
) -> None:
    epoch = published(replaying_broker, 3)
    opening = wire(cursor=Position(epoch, 1))
    frames = _resumed(opening, replaying_broker, 3, live=1)
    assert [frame.trace for frame in frames] == [2, 3, ("hello", 3), 4]
    assert {frame.epoch for frame in frames} == {epoch}


@pytest.mark.parametrize(
    ("step", "expected"),
    [("positions", [2, 3, 4, ("hello", 4), 4]), ("replay", [2, 3, ("hello", 3), 4])],
    ids=["before-snapshot", "during-replay"],
)
def test_live_message_around_replay_is_neither_lost_nor_applied_twice(
    wire: WireOpener,
    replaying_broker: BrokerBackend,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    expected: list[object],
) -> None:
    epoch = published(replaying_broker, 3)
    publish_during(replaying_broker, step, monkeypatch)
    opening = wire(cursor=Position(epoch, 1))
    frames = _resumed(opening, replaying_broker, len(expected))
    assert [frame.trace for frame in frames] == expected
    client = ResumingClient({ROOM_TOPIC: (epoch, 1)})
    for frame in frames:
        client.read(frame)
    assert client.applied == [(ROOM_TOPIC, epoch, seq) for seq in (2, 3, 4)]
    assert client.dropped == [(ROOM_TOPIC, epoch, 4)] * (len(expected) - 4)
    assert client.gaps == []
    assert client.cursors == {ROOM_TOPIC: (epoch, 4)}


def test_replayed_messages_pass_through_the_transform(
    wire: WireOpener, replaying_broker: BrokerBackend
) -> None:
    epoch = published(replaying_broker, 3)
    opening = wire(cursor=Position(epoch, 1), transform_ref=f"{_HERE}.skip_second")
    skipped, replayed, hello = _resumed(opening, replaying_broker, 3)
    assert (skipped.kind, skipped.epoch, skipped.seq) == ("skip", epoch, 2)
    assert replayed.seq == 3
    assert replayed.data["ops"][0]["name"] == "room:update"
    assert hello.trace == ("hello", 3)


def test_a_failing_transform_skips_its_message_on_replay_and_live(
    wire: WireOpener, replaying_broker: BrokerBackend, caplog: pytest.LogCaptureFixture
) -> None:
    epoch = published(replaying_broker, 3)
    opening = wire(cursor=Position(epoch, 1), transform_ref=f"{_HERE}.fail_even")
    frames = _resumed(opening, replaying_broker, 3, live=1)
    assert [frame.trace for frame in frames] == [
        ("skip", 2),
        3,
        ("hello", 3),
        ("skip", 4),
    ]
    client = ResumingClient({ROOM_TOPIC: (epoch, 1)})
    for frame in frames:
        client.read(frame)
    assert client.gaps == []
    assert client.cursors == {ROOM_TOPIC: (epoch, 4)}
    failures = [
        record.getMessage()
        for record in caplog.records
        if record.name == "next.realtime.delivery"
    ]
    assert failures == [
        f"Realtime transform failed on {ROOM_TOPIC} seq 2, skipping it",
        f"Realtime transform failed on {ROOM_TOPIC} seq 4, skipping it",
    ]


def test_an_unsafe_transform_payload_skips_its_message(
    wire: WireOpener, replaying_broker: BrokerBackend
) -> None:
    epoch = published(replaying_broker, 2)
    opening = wire(cursor=Position(epoch, 1), transform_ref=f"{_HERE}.unsafe_payload")
    frames = _resumed(opening, replaying_broker, 2, live=1)
    assert [frame.trace for frame in frames] == [("skip", 2), ("hello", 2), ("skip", 3)]


def test_access_revoked_during_replay_ends_before_hello(
    wire: WireOpener, replaying_broker: BrokerBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    epoch = published(replaying_broker, 3)
    verdicts = iter([Access.GRANTED, Access.GRANTED, Access.TOPIC_DENIED])

    async def acheck(self: AccessCheck) -> Access:
        return next(verdicts)

    monkeypatch.setattr(AccessCheck, "check", lambda _self: next(verdicts))
    monkeypatch.setattr(AccessCheck, "acheck", acheck)
    opening = wire(cursor=Position(epoch, 1))

    async def scenario() -> list[object]:
        async with opening as client:
            frames = await client.frames(2)
            return [*(frame.trace for frame in frames), await client.ends()]

    closed = Close.TOPIC_DENIED if wire.transport == "ws" else None
    assert asyncio.run(scenario()) == [2, ("end", "auth"), closed]


def test_broker_without_replay_goes_straight_to_hello(
    wire: WireOpener, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = PlainBroker()
    monkeypatch.setattr(broker_manager, "_backend", broker)
    epoch = published(broker, 3)
    opening = wire(cursor=Position(epoch, 1))
    frames = _resumed(opening, broker, 1, live=1)
    assert [frame.trace for frame in frames] == [("hello", 3), 4]
