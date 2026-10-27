import asyncio
import time
from collections.abc import Callable, Iterator

import fakeredis
import pytest

from next.realtime import (
    InvalidMemberError,
    InvalidTopicError,
    MemoryBroker,
    Position,
    Presence,
)
from next.realtime.roster import EVENT, Member, PresenceSchedule, PresenceWindow
from next.realtime.signals import message_published
from next.testing import SignalRecorder, capture_signals
from tests.support.realtime import (
    FakeServerBroker,
    ServerClock,
    freeze_fakeredis,
    manual_schedule,
    refresh_envelope,
)


TOPIC = "room:1"
WINDOW = 1.0
PAST_WINDOW = WINDOW + 0.01


class _Api:
    """Drive a broker through its sync methods or their async twins on one loop."""

    def __init__(
        self, broker: MemoryBroker | FakeServerBroker, *, asynchronous: bool
    ) -> None:
        self.broker = broker
        self.loop = asyncio.new_event_loop() if asynchronous else None

    def _call(self, name: str, *args: object) -> object:
        if self.loop is None:
            return getattr(self.broker, name)(*args)
        return self.loop.run_until_complete(getattr(self.broker, f"a{name}")(*args))

    def join(
        self, topic: str, connection: str, member: Member | None, ttl: float = 30
    ) -> None:
        self._call("join", topic, connection, member, ttl)

    def touch(self, topic: str, connection: str, ttl: float = 30) -> bool:
        return bool(self._call("touch", topic, connection, ttl))

    def leave(self, topic: str, connection: str) -> None:
        self._call("leave", topic, connection)

    def presence(self, topic: str = TOPIC) -> Presence:
        snapshot = self._call("presence", topic)
        assert isinstance(snapshot, Presence)
        return snapshot

    def pending(self) -> tuple[str, ...]:
        return self.broker.presence_schedule.pending()

    def close_window(self, clock: ServerClock) -> int:
        clock.advance(PAST_WINDOW)
        return self.broker.presence_schedule.run_due()

    def close(self) -> None:
        if self.loop is None:
            self.broker.close()
            return
        self.loop.run_until_complete(self.broker.aclose())
        self.loop.close()


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> ServerClock:
    clock = ServerClock()
    freeze_fakeredis(monkeypatch, clock)
    return clock


@pytest.fixture(params=["memory", "redis"])
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(params=["sync", "async"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture()
def build(kind: str, mode: str, clock: ServerClock) -> Iterator[Callable[..., _Api]]:
    server = fakeredis.FakeServer()
    apis: list[_Api] = []

    def make(**presence: object) -> _Api:
        limits = {"WINDOW": WINDOW, **presence}
        broker: MemoryBroker | FakeServerBroker
        schedule = manual_schedule(clock)
        if kind == "memory":
            broker = MemoryBroker(clock=clock, presence=limits, schedule=schedule)
        else:
            broker = FakeServerBroker(
                server, presence=limits, clock=clock, schedule=schedule
            )
        api = _Api(broker, asynchronous=mode == "async")
        apis.append(api)
        return api

    yield make
    for api in apis:
        api.close()


@pytest.fixture()
def snapshots() -> Iterator[SignalRecorder]:
    with capture_signals(message_published) as recorder:
        yield recorder


def _announced(recorder: SignalRecorder) -> list[tuple[str, object]]:
    announced = []
    for event in recorder:
        (op,) = event.kwargs["message"].envelope.ops
        if op.extras.get("name") == EVENT:
            announced.append((event.kwargs["topic"], op.extras["detail"]))
    return announced


def _ids(snapshot: Presence) -> list[object]:
    return sorted(member["id"] for member in snapshot.members)


def test_join_touch_and_leave_round_trip_in_count_mode(build) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    api.join(TOPIC, "b", None)

    assert api.presence() == Presence(2)
    assert api.touch(TOPIC, "a")
    assert not api.touch(TOPIC, "missing")
    api.leave(TOPIC, "a")
    api.leave(TOPIC, "missing")
    assert api.presence() == Presence(1)
    assert api.presence("room:2") == Presence(0)


def test_members_mode_lists_each_identified_member(build) -> None:
    api = build()
    for index in (1, 2, 3):
        api.join(TOPIC, f"c{index}", {"id": index})

    snapshot = api.presence()
    assert snapshot.count == 3
    assert _ids(snapshot) == [1, 2, 3]
    api.leave(TOPIC, "c2")
    assert _ids(api.presence()) == [1, 3]


@pytest.mark.parametrize(("size", "listed"), [(2, 2), (0, 0)])
def test_snapshot_caps_members_at_size_but_keeps_the_count(
    build, size: int, listed: int
) -> None:
    api = build(SIZE=size)
    for index in range(5):
        api.join(TOPIC, f"c{index}", {"id": index})

    snapshot = api.presence()
    assert snapshot.count == 5
    assert len(snapshot.members) == listed
    assert set(_ids(snapshot)) <= set(range(5))


def test_member_above_member_bytes_is_refused_and_not_recorded(build) -> None:
    api = build(MEMBER_BYTES=32)
    exact = {"n": "x" * 24}
    api.join(TOPIC, "exact", exact)

    with pytest.raises(InvalidMemberError, match="above the limit of 32"):
        api.join(TOPIC, "big", {"n": "x" * 25})
    with pytest.raises(InvalidMemberError, match="not a mapping"):
        api.join(TOPIC, "list", ["Ada"])
    with pytest.raises(InvalidMemberError, match="lone surrogate"):
        api.join(TOPIC, "surrogate", {"n": "\ud800"})
    with pytest.raises(InvalidMemberError, match="lone surrogate"):
        api.join(TOPIC, "surrogate", {"\udfff": 1})
    assert api.presence() == Presence(1, (exact,))
    assert not api.touch(TOPIC, "big")


def test_invalid_topic_is_refused_by_every_method(build) -> None:
    api = build()
    for call in (
        lambda: api.join("", "a", None),
        lambda: api.touch("", "a"),
        lambda: api.leave("", "a"),
        lambda: api.presence(""),
    ):
        with pytest.raises(InvalidTopicError):
            call()


def test_hundred_joins_inside_one_window_announce_one_final_snapshot(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    for index in range(100):
        api.join(TOPIC, f"c{index}", None)
        clock.advance(WINDOW / 200)

    assert api.pending() == (TOPIC,)
    assert _announced(snapshots) == []
    assert api.close_window(clock) == 1
    assert _announced(snapshots) == [(TOPIC, {"count": 100})]
    assert api.broker.positions([TOPIC])[TOPIC].seq == 0
    assert api.broker.presence_schedule.run_due() == 0


def test_members_snapshot_carries_the_identified_members(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "a", {"id": 1})
    api.join(TOPIC, "b", {"id": 2})
    api.close_window(clock)

    ((topic, detail),) = _announced(snapshots)
    assert topic == TOPIC
    assert detail["count"] == 2
    assert sorted(member["id"] for member in detail["members"]) == [1, 2]


def test_count_mode_snapshot_never_carries_members(
    build, clock: ServerClock, snapshots
) -> None:
    api = build(SIZE=0)
    api.join(TOPIC, "a", {"id": 1})
    api.join(TOPIC, "b", None)
    api.close_window(clock)

    assert _announced(snapshots) == [(TOPIC, {"count": 2})]


def test_change_after_the_window_closed_opens_the_next_one(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    api.close_window(clock)
    api.join(TOPIC, "b", None)
    api.leave(TOPIC, "a")

    assert api.pending() == (TOPIC,)
    api.close_window(clock)
    assert _announced(snapshots) == [(TOPIC, {"count": 1}), (TOPIC, {"count": 1})]


def test_unchanged_rejoin_opens_no_window_but_a_new_member_does(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "a", {"id": 1})
    api.close_window(clock)

    api.join(TOPIC, "a", {"id": 1})
    assert api.pending() == ()
    assert api.touch(TOPIC, "a")
    assert api.pending() == ()
    api.join(TOPIC, "a", {"id": 2})
    assert api.pending() == (TOPIC,)


def test_unchanged_rejoin_without_a_member_opens_no_window(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    api.close_window(clock)

    api.join(TOPIC, "a", None)
    assert api.pending() == ()
    assert api.presence() == Presence(1)


def test_record_left_without_leave_expires_and_announces(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "crashed", None, 3)
    api.join(TOPIC, "alive", None, 30)
    api.close_window(clock)
    clock.advance(3)

    assert api.presence() == Presence(1)
    assert api.pending() == ()
    assert api.touch(TOPIC, "alive")
    assert api.pending() == (TOPIC,)
    api.close_window(clock)
    assert _announced(snapshots) == [(TOPIC, {"count": 2}), (TOPIC, {"count": 1})]
    assert not api.touch(TOPIC, "crashed")


def test_touch_keeps_a_record_past_its_first_deadline(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "a", None, 3)
    clock.advance(2)
    assert api.touch(TOPIC, "a", 3)
    clock.advance(2)

    assert api.presence() == Presence(1)
    clock.advance(2)
    assert api.presence() == Presence(0)


def test_topics_keep_separate_rosters_and_windows(build, clock: ServerClock) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    api.join("room:2", "a", None)
    api.leave(TOPIC, "a")

    assert api.presence() == Presence(0)
    assert api.presence("room:2") == Presence(1)
    assert set(api.pending()) == {TOPIC, "room:2"}


def test_connections_with_an_equal_member_count_as_one_attendee(build) -> None:
    api = build()
    api.join(TOPIC, "tab1", {"id": 1, "name": "Ada"})
    api.join(TOPIC, "tab2", {"name": "Ada", "id": 1})
    api.join(TOPIC, "other", {"id": 2})
    api.join(TOPIC, "guest", None)

    snapshot = api.presence()
    assert snapshot.count == 3
    assert _ids(snapshot) == [1, 2]


def test_second_connection_of_a_member_announces_for_its_own_subscriber(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "tab1", {"id": 1})
    api.close_window(clock)
    api.join(TOPIC, "tab2", {"id": 1})

    assert api.pending() == (TOPIC,)
    api.close_window(clock)
    member = {"count": 1, "members": [{"id": 1}]}
    assert _announced(snapshots) == [(TOPIC, member), (TOPIC, member)]


def test_leaving_one_connection_of_a_member_keeps_it_counted(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "tab1", {"id": 1})
    api.join(TOPIC, "tab2", {"id": 1})
    api.close_window(clock)

    api.leave(TOPIC, "tab1")
    assert api.presence() == Presence(1, ({"id": 1},))
    assert api.pending() == ()
    api.leave(TOPIC, "tab2")
    assert api.presence() == Presence(0)
    assert api.pending() == (TOPIC,)


def test_expired_connection_of_a_member_keeps_it_counted_silently(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "crashed", {"id": 1}, 3)
    api.join(TOPIC, "alive", {"id": 1}, 30)
    api.close_window(clock)
    clock.advance(3)

    assert api.presence() == Presence(1, ({"id": 1},))
    assert api.touch(TOPIC, "alive")
    assert api.pending() == ()
    assert not api.touch(TOPIC, "crashed")
    assert api.presence() == Presence(1, ({"id": 1},))


def test_member_lapses_with_its_last_connection_after_the_longest_one_left(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "long", {"id": 1}, 30)
    api.join(TOPIC, "short", {"id": 1}, 3)
    api.leave(TOPIC, "long")
    clock.advance(3)

    assert api.presence() == Presence(0)


def test_member_whose_connections_all_expired_is_pruned_and_announced(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "tab1", {"id": 1}, 3)
    api.join(TOPIC, "tab2", {"id": 1}, 3)
    api.join(TOPIC, "alive", None, 30)
    api.close_window(clock)
    clock.advance(3)

    assert api.touch(TOPIC, "alive")
    assert api.pending() == (TOPIC,)
    api.close_window(clock)
    assert _announced(snapshots)[-1] == (TOPIC, {"count": 1})
    assert not api.touch(TOPIC, "tab1")


def test_connection_that_changes_member_moves_between_attendees(build) -> None:
    api = build()
    api.join(TOPIC, "a", {"id": 1})
    api.join(TOPIC, "b", {"id": 1})

    api.join(TOPIC, "a", {"id": 2})
    assert api.presence().count == 2
    assert _ids(api.presence()) == [1, 2]
    api.join(TOPIC, "a", None)
    assert api.presence() == Presence(2, ({"id": 1},))
    api.join(TOPIC, "a", {"id": 1})
    assert api.presence() == Presence(1, ({"id": 1},))
    api.leave(TOPIC, "b")
    assert api.presence() == Presence(1, ({"id": 1},))


def test_a_change_exactly_at_the_window_end_claims_the_next_window(
    clock: ServerClock,
) -> None:
    broker = MemoryBroker(
        clock=clock, presence={"WINDOW": WINDOW}, schedule=manual_schedule(clock)
    )
    broker.join(TOPIC, "a", None, 30)
    first_end = clock() + WINDOW
    assert broker._windows[TOPIC] == first_end

    clock.advance(WINDOW - 0.25)
    broker.join(TOPIC, "b", None, 30)
    assert broker._windows[TOPIC] == first_end
    clock.advance(0.25)
    broker.join(TOPIC, "c", None, 30)
    assert broker._windows[TOPIC] == first_end + WINDOW


def _lose_the_schedule(api: _Api, clock: ServerClock) -> None:
    api.broker.presence_schedule = PresenceSchedule(
        api.broker.announce_presence, clock=clock, threaded=False
    )


def test_touch_after_a_lost_window_claims_the_unannounced_change(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    _lose_the_schedule(api, clock)
    clock.advance(PAST_WINDOW)

    assert api.touch(TOPIC, "a")
    assert api.pending() == (TOPIC,)
    api.close_window(clock)
    assert _announced(snapshots) == [(TOPIC, {"count": 1})]
    assert api.touch(TOPIC, "a")
    assert api.pending() == ()


def test_unchanged_join_and_leave_claim_an_unannounced_change(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    _lose_the_schedule(api, clock)
    clock.advance(PAST_WINDOW)
    api.join(TOPIC, "a", None)

    assert api.pending() == (TOPIC,)
    _lose_the_schedule(api, clock)
    clock.advance(PAST_WINDOW)
    api.leave(TOPIC, "missing")
    assert api.pending() == (TOPIC,)
    api.close_window(clock)
    assert _announced(snapshots) == [(TOPIC, {"count": 1})]


def test_window_already_announced_publishes_nothing_twice(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    api.broker.presence_schedule.schedule(TOPIC, WINDOW)

    assert api.close_window(clock) == 2
    assert _announced(snapshots) == [(TOPIC, {"count": 1})]


def test_snapshot_is_unnumbered_and_stays_out_of_the_replay_log(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    real = api.broker.publish(TOPIC, refresh_envelope())
    for index in range(12):
        api.join(TOPIC, f"c{index}", None)
        api.close_window(clock)

    assert len(_announced(snapshots)) == 12
    assert {event.kwargs["message"].seq for event in snapshots} == {0}
    assert api.broker.positions([TOPIC])[TOPIC] == Position(real.epoch, real.seq)
    replayed = api.broker.replay(TOPIC, Position(real.epoch, 0))
    assert [message.seq for message in replayed.messages] == [real.seq]


def test_shorter_rejoin_of_a_member_lets_it_expire_on_the_new_deadline(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "tab", {"n": "a"}, 75)
    api.join(TOPIC, "tab", {"n": "a"}, 3)
    clock.advance(10)

    assert api.presence() == Presence(0)


def test_shorter_touch_keeps_the_member_until_its_longest_connection(
    build, clock: ServerClock
) -> None:
    api = build()
    api.join(TOPIC, "long", {"n": "a"}, 30)
    api.join(TOPIC, "short", {"n": "a"}, 75)
    assert api.touch(TOPIC, "long", 3)
    assert api.touch(TOPIC, "short", 20)
    clock.advance(10)

    assert api.presence() == Presence(1, ({"n": "a"},))
    clock.advance(15)
    assert api.presence() == Presence(0)


def test_members_keep_the_order_of_their_first_join(build, clock: ServerClock) -> None:
    api = build()
    for index in (3, 1, 2):
        api.join(TOPIC, f"c{index}", {"id": index}, 30)
        clock.advance(0.01)
    for index, ttl in ((2, 60), (1, 40), (3, 20)):
        assert api.touch(TOPIC, f"c{index}", ttl)
    api.join(TOPIC, "c4", {"id": 1}, 90)

    assert [member["id"] for member in api.presence().members] == [3, 1, 2]
    api.leave(TOPIC, "c3")
    api.join(TOPIC, "c3", {"id": 3}, 30)
    assert [member["id"] for member in api.presence().members] == [1, 2, 3]


def test_presence_returns_members_a_caller_may_change(build) -> None:
    api = build()
    api.join(TOPIC, "a", {"tags": ["x"]})
    snapshot = api.presence()
    snapshot.members[0]["tags"].append("y")

    assert api.presence() == Presence(1, ({"tags": ["x"]},))


def test_closing_the_broker_publishes_the_pending_snapshot(
    build, clock: ServerClock, snapshots
) -> None:
    api = build()
    api.join(TOPIC, "a", None)
    api.broker.close()

    assert _announced(snapshots) == [(TOPIC, {"count": 1})]


def _on_a_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _broker(kind: str, clock: ServerClock) -> MemoryBroker | FakeServerBroker:
    limits = {"WINDOW": WINDOW}
    schedule = manual_schedule(clock)
    if kind == "memory":
        return MemoryBroker(clock=clock, presence=limits, schedule=schedule)
    return FakeServerBroker(
        fakeredis.FakeServer(), presence=limits, clock=clock, schedule=schedule
    )


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_aclose_announces_the_pending_snapshot_through_the_async_send(
    kind: str, clock: ServerClock
) -> None:
    broker = _broker(kind, clock)
    calls: list[tuple[str, object]] = []

    async def areceiver(sender: object, topic: str, **kwargs: object) -> None:
        calls.append(("async", topic))

    def receiver(sender: object, topic: str, **kwargs: object) -> None:
        calls.append(("sync", _on_a_loop()))

    async def scenario() -> None:
        await broker.ajoin(TOPIC, "a", None, 30)
        assert broker.presence_schedule.pending() == (TOPIC,)
        await broker.aclose()

    message_published.connect(areceiver)
    message_published.connect(receiver)
    try:
        asyncio.run(scenario())
    finally:
        message_published.disconnect(areceiver)
        message_published.disconnect(receiver)
    assert sorted(calls, key=str) == [("async", TOPIC), ("sync", False)]
    assert broker.presence_schedule.pending() == ()


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_aclose_skips_a_pending_topic_already_announced(
    kind: str, clock: ServerClock, snapshots
) -> None:
    broker = _broker(kind, clock)

    async def scenario() -> None:
        await broker.ajoin(TOPIC, "a", None, 30)
        clock.advance(PAST_WINDOW)
        broker.presence_schedule.run_due()
        broker.presence_schedule.schedule(TOPIC, WINDOW)
        await broker.aclose()

    asyncio.run(scenario())
    assert _announced(snapshots) == [(TOPIC, {"count": 1})]


def test_aclose_logs_a_snapshot_that_fails_and_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    published: list[str] = []
    schedule = PresenceSchedule(published.append, threaded=False)
    schedule.schedule("room:a", 1)
    schedule.schedule("room:b", 2)

    async def publish(topic: str) -> None:
        if topic == "room:a":
            raise ConnectionError
        published.append(topic)

    asyncio.run(schedule.aclose(publish))
    assert published == ["room:b"]
    assert (
        caplog.records[0].getMessage() == "Could not publish the presence of 'room:a'."
    )
    schedule.schedule("room:c", 1)
    assert schedule.pending() == ()


class _SyncOnlyWindow(PresenceWindow):
    def __init__(self) -> None:
        self.announced: list[tuple[str, bool]] = []
        self.keep_presence(None, None, time.monotonic)

    def announce_presence(self, topic: str) -> None:
        self.announced.append((topic, _on_a_loop()))


def test_a_window_without_an_async_announce_runs_the_sync_one_off_the_loop() -> None:
    window = _SyncOnlyWindow()
    asyncio.run(window.aannounce_presence(TOPIC))
    assert window.announced == [(TOPIC, False)]
