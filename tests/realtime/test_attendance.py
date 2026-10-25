import asyncio
import logging
import threading

import fakeredis
import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncRequestFactory, RequestFactory, override_settings

from next.realtime import (
    InvalidMemberError,
    MemoryBroker,
    Message,
    Presence,
    PresenceBroker,
    TopicStream,
    apresence,
    presence,
)
from next.realtime.attendance import Attendance
from next.realtime.checks import check_realtime_settings
from next.realtime.manager import broker_manager
from next.realtime.roster import (
    EVENT,
    PresenceSchedule,
    decode_member,
    encode_member,
    presence_limits,
)
from tests.support.realtime import (
    FakeServerBroker,
    PlainBroker,
    manual_schedule,
    next_frame,
    silent_schedule,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _identify(user: object, topic: str) -> dict[str, object]:
    return {"name": "guest", "topic": topic}


@pytest.fixture()
def clock() -> _Clock:
    return _Clock()


@pytest.fixture()
def memory(clock: _Clock) -> MemoryBroker:
    return MemoryBroker(clock=clock, schedule=manual_schedule(clock))


@pytest.fixture()
def redis_broker() -> FakeServerBroker:
    return FakeServerBroker(fakeredis.FakeServer(), schedule=silent_schedule)


def test_both_brokers_implement_presence(memory, redis_broker) -> None:
    assert isinstance(memory, PresenceBroker)
    assert isinstance(redis_broker, PresenceBroker)
    assert not isinstance(PlainBroker(), PresenceBroker)


def test_memory_join_counts_every_connection_and_lists_identified_ones(
    memory: MemoryBroker,
) -> None:
    memory.join("room:1", "a", {"name": "Ada"}, 30)
    memory.join("room:1", "b", None, 30)

    assert memory.presence("room:1") == Presence(2, ({"name": "Ada"},))


def test_memory_changes_inside_one_window_schedule_one_snapshot(
    memory: MemoryBroker, clock: _Clock
) -> None:
    memory.join("room:1", "a", None, 30)
    memory.join("room:1", "b", None, 30)
    memory.leave("room:1", "a")

    assert memory.presence_schedule.pending() == ("room:1",)
    clock.now += 1
    with memory.subscribe(["room:1"], timeout=0.01) as stream:
        assert memory.presence_schedule.run_due() == 1
        message = next(stream)
    (op,) = message.envelope.ops
    assert op.op == "event"
    assert op.extras == {"name": EVENT, "detail": {"count": 1}}


def test_memory_touch_refuses_an_expired_record_and_prunes_it(
    memory: MemoryBroker, clock: _Clock
) -> None:
    memory.join("room:1", "a", None, 3)
    memory.join("room:1", "b", None, 30)
    clock.now += 2
    assert memory.touch("room:1", "a", 3)
    clock.now += 4

    assert memory.presence("room:1").count == 1
    assert not memory.touch("room:1", "a", 3)
    assert memory.presence("room:1").count == 1


def test_memory_presence_caps_members_but_keeps_the_count(clock: _Clock) -> None:
    broker = MemoryBroker(clock=clock, presence={"SIZE": 2})
    for index in range(5):
        broker.join("room:1", f"c{index}", {"index": index}, 30)

    snapshot = broker.presence("room:1")
    assert snapshot.count == 5
    assert len(snapshot.members) == 2
    broker.close()


def test_redis_join_touch_and_leave_round_trip(redis_broker) -> None:
    redis_broker.join("room:1", "a", {"name": "Ada"}, 30)
    redis_broker.join("room:1", "b", None, 30)

    assert redis_broker.presence("room:1") == Presence(2, ({"name": "Ada"},))
    assert redis_broker.touch("room:1", "a", 30)
    assert not redis_broker.touch("room:1", "missing", 30)
    redis_broker.leave("room:1", "a")
    assert redis_broker.presence("room:1") == Presence(1)


def test_redis_window_lock_lets_only_the_first_change_schedule(redis_broker) -> None:
    redis_broker.join("room:1", "a", None, 30)
    redis_broker.join("room:1", "b", None, 30)

    assert redis_broker.presence_schedule.pending() == ("room:1",)
    assert redis_broker._redis.pttl("next:window:{room:1}") > 0


def test_redis_async_api_matches_the_sync_one(redis_broker) -> None:
    async def scenario() -> tuple[Presence, bool, Presence]:
        await redis_broker.ajoin("room:2", "x", {"n": 1}, 30)
        joined = await redis_broker.apresence("room:2")
        touched = await redis_broker.atouch("room:2", "x", 30)
        await redis_broker.aleave("room:2", "x")
        return joined, touched, await redis_broker.apresence("room:2")

    joined, touched, left = asyncio.run(scenario())
    assert joined == Presence(1, ({"n": 1},))
    assert touched
    assert left == Presence(0)


@pytest.mark.parametrize(
    ("member", "reason"),
    [
        ({"name": "x" * 600}, "above the limit"),
        ({"value": float("nan")}, "JSON cannot carry"),
        (["Ada"], "not a mapping"),
    ],
)
def test_encode_member_refuses_what_a_snapshot_cannot_carry(
    member: object, reason: str
) -> None:
    with pytest.raises(InvalidMemberError, match=reason):
        encode_member(member, 512)


@pytest.mark.parametrize(
    "limits", [{"WINDOW": 0}, {"SIZE": -1}, {"MEMBER_BYTES": 0}, {"OTHER": 1}, []]
)
def test_presence_limits_refuse_invalid_settings(limits: object) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"REALTIME\.PRESENCE"):
        presence_limits(limits)


def test_threaded_schedule_publishes_once_the_window_closes() -> None:
    published = threading.Event()
    schedule = PresenceSchedule(lambda _topic: published.set())
    schedule.schedule("room:1", 0.01)

    assert published.wait(5)
    schedule.close()


def test_schedule_logs_a_failed_publication_and_keeps_going(caplog) -> None:
    def fail(topic: str) -> None:
        raise ConnectionError(topic)

    schedule = PresenceSchedule(fail, clock=lambda: 0.0, threaded=False)
    schedule.schedule("room:1", 0)

    assert schedule.run_due() == 1
    assert "Could not publish the presence" in caplog.text


def test_attendance_rejoins_when_a_touch_finds_no_record(memory, clock) -> None:
    attendance = Attendance(memory, {"room:1": None}, heartbeat=1, clock=clock)
    attendance.join()
    clock.now += 5
    attendance.touch()

    assert memory.presence("room:1").count == 1
    attendance.leave()
    assert memory.presence("room:1").count == 0


@pytest.mark.django_db()
def test_sync_stream_joins_before_hello_and_leaves_on_close(
    memory: MemoryBroker, monkeypatch
) -> None:
    monkeypatch.setattr(broker_manager, "_backend", memory)
    request = RequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    stream = TopicStream(request, "room:1", presence=_identify, heartbeat_seconds=5)
    iterator = iter(stream.streaming_content)
    next(iterator)
    assert b"hello" in next_frame(iterator)

    assert memory.presence("room:1") == Presence(
        1, ({"name": "guest", "topic": "room:1"},)
    )
    stream.close()
    assert memory.presence("room:1").count == 0


def test_async_stream_leaves_when_its_task_is_cancelled(
    memory: MemoryBroker, monkeypatch
) -> None:
    monkeypatch.setattr(broker_manager, "_backend", memory)
    request = AsyncRequestFactory().get("/rooms/1/stream/")
    stream = TopicStream(request, "room:1", presence=True, heartbeat_seconds=5)
    source = stream._async_messages(memory)

    async def scenario() -> tuple[int, int]:
        hello = await anext(source)
        assert hello is not None
        snapshot = await anext(source)
        assert isinstance(snapshot, Message)
        assert not snapshot.numbered
        joined = memory.presence("room:1").count
        pull = asyncio.ensure_future(anext(source))
        await asyncio.sleep(0)
        pull.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pull
        return joined, memory.presence("room:1").count

    assert asyncio.run(scenario()) == (1, 0)


def test_stream_presence_on_a_broker_without_it_refuses_to_start(monkeypatch) -> None:
    monkeypatch.setattr(broker_manager, "_backend", PlainBroker())
    request = RequestFactory().get("/rooms/1/stream/")

    with pytest.raises(ImproperlyConfigured, match="keeps no presence"):
        TopicStream(request, "room:1", presence=True)


def test_public_presence_reads_the_configured_broker(memory, monkeypatch) -> None:
    monkeypatch.setattr(broker_manager, "_backend", memory)
    memory.join("room:1", "a", None, 30)

    assert presence("room:1") == Presence(1)
    assert asyncio.run(apresence("room:1")) == Presence(1)
    monkeypatch.setattr(broker_manager, "_backend", PlainBroker())
    with pytest.raises(ImproperlyConfigured):
        presence("room:1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([], {"next.E193"}),
        ({"WINDOW": 0}, {"next.E194"}),
        ({"SIZE": 5000}, {"next.E195"}),
        ({"MEMBER_BYTES": True}, {"next.E196"}),
        ({"WINDOW": 0.5, "SIZE": 0, "MEMBER_BYTES": 64}, set()),
    ],
)
def test_check_reports_invalid_presence_settings(value: object, expected) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"PRESENCE": value}}):
        assert {item.id for item in check_realtime_settings()} == expected


def test_check_warns_presence_settings_on_a_broker_without_presence() -> None:
    backend = {"BACKEND": "tests.support.realtime.PlainBroker"}
    config = {"REALTIME": {"BROKER": backend, "PRESENCE": {}, "REPLAY": {"SIZE": 0}}}
    with override_settings(NEXT_FRAMEWORK=config):
        (warning,) = check_realtime_settings()
    assert warning.id == "next.W144"


@pytest.mark.parametrize("payload", [b"{broken", '"text"', None])
def test_decode_member_skips_a_malformed_record(payload: object, caplog) -> None:
    assert decode_member(payload) is None
    assert "Skipped a malformed presence member" in caplog.text


def test_closed_schedule_ignores_a_new_window() -> None:
    schedule = PresenceSchedule(lambda _topic: None, threaded=False)
    schedule.close()
    schedule.schedule("room:1", 0)

    assert schedule.pending() == ()


def test_sync_refresh_touches_once_a_heartbeat_has_passed(memory, clock) -> None:
    attendance = Attendance(memory, {"room:1": None}, heartbeat=1, clock=clock)
    attendance.join()
    (joined,) = memory._rosters["room:1"].anonymous.values()

    def items():
        yield "early"
        clock.now += 2
        yield "late"

    assert list(attendance.refreshed(items())) == ["early", "late"]
    (touched,) = memory._rosters["room:1"].anonymous.values()
    assert touched > joined


def test_async_touch_rejoins_a_pruned_record(memory, clock) -> None:
    attendance = Attendance(memory, {"room:1": None}, heartbeat=1, clock=clock)

    async def scenario() -> int:
        await attendance.ajoin()
        clock.now += 5
        await attendance.atouch()
        return memory.presence("room:1").count

    assert asyncio.run(scenario()) == 1


def test_memory_touch_and_leave_on_a_topic_without_records(memory, clock) -> None:
    assert not memory.touch("room:1", "a", 3)
    memory.leave("room:1", "a")
    memory.join("room:1", "a", None, 3)
    clock.now += 5

    assert not memory.touch("room:1", "a", 3)
    assert "room:1" in memory._rosters
    with memory.subscribe(["room:1"], timeout=0.01) as stream:
        memory.presence_schedule.run_due()
        message = next(stream)
    assert message.envelope.ops[0].extras["detail"] == {"count": 0}
    assert "room:1" not in memory._rosters


def test_memory_leave_after_the_window_claims_a_new_snapshot(memory, clock) -> None:
    memory.join("room:1", "a", None, 30)
    clock.now += 1
    memory.presence_schedule.run_due()
    clock.now += 1
    memory.leave("room:1", "a")

    assert memory.presence_schedule.pending() == ("room:1",)


class _HangingBroker(MemoryBroker):
    async def aleave(self, topic: str, connection: str) -> None:
        await asyncio.sleep(60)


def test_async_leave_gives_up_after_one_heartbeat(caplog) -> None:
    broker = _HangingBroker(clock=lambda: 0.0, schedule=silent_schedule)
    attendance = Attendance(broker, {"room:1": None}, heartbeat=0.01)
    attendance.join()
    with caplog.at_level(logging.WARNING, logger="next.realtime.attendance"):
        asyncio.run(attendance.aleave())

    assert "did not confirm a leave in time" in caplog.text
    assert broker.presence("room:1").count == 1


class _LongAttendance(Attendance):
    ttl_heartbeats = 10


class _LongStream(TopicStream):
    def attend(self, backend: MemoryBroker) -> Attendance | None:
        return _LongAttendance(backend, dict.fromkeys(self.topics), heartbeat=5)


@pytest.mark.django_db()
def test_stream_attend_hook_keeps_its_own_attendance(
    memory: MemoryBroker, clock: _Clock, monkeypatch
) -> None:
    monkeypatch.setattr(broker_manager, "_backend", memory)
    request = RequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    stream = _LongStream(request, "room:1", presence=True, heartbeat_seconds=5)
    iterator = iter(stream.streaming_content)
    next(iterator)
    assert b"hello" in next_frame(iterator)

    assert list(memory._rosters["room:1"].anonymous.values()) == [clock.now + 50]
    stream.close()
    assert memory.presence("room:1").count == 0


class _Recording(MemoryBroker):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.announced: list[str] = []

    def announce_presence(self, topic: str) -> None:
        self.announced.append(topic)
        super().announce_presence(topic)


def test_broker_announce_hook_receives_every_closed_window(clock: _Clock) -> None:
    broker = _Recording(clock=clock, schedule=manual_schedule(clock))
    broker.join("room:1", "a", None, 30)
    clock.now += 2

    assert broker.presence_schedule.run_due() == 1
    assert broker.announced == ["room:1"]
