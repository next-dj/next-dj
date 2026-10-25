import asyncio
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from types import MappingProxyType

import fakeredis
import pytest
import redis
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncRequestFactory, RequestFactory, override_settings

from next.partial import Envelope, Patch, Patches
from next.realtime import (
    BrokerConnectionError,
    InvalidMemberError,
    MemoryBroker,
    Message,
    Presence,
    TopicStream,
    redis as redis_module,
    roster as roster_module,
)
from next.realtime.attendance import Attendance
from next.realtime.manager import broker_manager
from next.realtime.roster import (
    EVENT,
    PresenceSchedule,
    announce,
    counted,
    encode_member,
    presence_envelope,
    snapshot_message,
)
from next.realtime.signals import message_published
from next.realtime.socket import Handshake, TopicSocket
from next.testing import SseEvent, SseReader, capture_signals
from next.testing.realtime import MemoryPort, SocketTicket
from tests.support.realtime import (
    FakeServerBroker,
    PlainBroker,
    ServerClock,
    freeze_fakeredis,
    issue_session_ticket,
    manual_schedule,
    refresh_envelope,
    silent_schedule,
)


TOPIC = "room:42"
WINDOW = 1.0
_HERE = "tests.realtime.test_snapshots"


def identify(user: object, topic: str) -> dict[str, object]:
    return {"handle": "alice", "email": "a@example.com"}


def skip_everything(message: Message) -> None:
    del message


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> ServerClock:
    clock = ServerClock()
    freeze_fakeredis(monkeypatch, clock)
    return clock


@pytest.fixture()
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


def _redis(server: fakeredis.FakeServer, clock: ServerClock) -> FakeServerBroker:
    return FakeServerBroker(
        server,
        presence={"WINDOW": WINDOW},
        clock=clock,
        schedule=manual_schedule(clock),
    )


def _memory(**presence: object) -> MemoryBroker:
    return MemoryBroker(
        presence={"WINDOW": WINDOW, **presence}, schedule=manual_schedule(lambda: 0.0)
    )


def _details(recorder) -> list[object]:
    return [
        event.kwargs["message"].envelope.ops[0].extras["detail"] for event in recorder
    ]


def _first(stream: Iterator[Message | None]) -> Message:
    deadline = time.monotonic() + 5
    for item in stream:
        if item is not None:
            return item
        if time.monotonic() > deadline:
            break
    pytest.fail("No message arrived on the subscription within five seconds.")


def _until_idle(stream: Iterator[Message | None]) -> list[Message]:
    received: list[Message] = []
    for _ in range(500):
        item = next(stream)
        if item is None and received:
            return received
        if item is not None:
            received.append(item)
    pytest.fail("The subscription never went idle after its messages.")


def test_snapshot_survives_the_death_of_the_process_owning_the_window(
    server, clock
) -> None:
    owner, other = _redis(server, clock), _redis(server, clock)
    owner.join(TOPIC, "c3", None, 30)
    owner.presence_schedule = PresenceSchedule(lambda _topic: None, threaded=False)
    other.join(TOPIC, "c2", None, 30)

    assert other.presence_schedule.pending() == ()
    clock.advance(WINDOW + 0.01)
    with capture_signals(message_published) as recorder:
        assert other.touch(TOPIC, "c2", 30)
        assert other.presence_schedule.pending() == (TOPIC,)
        clock.advance(WINDOW + 0.01)
        assert other.presence_schedule.run_due() == 1
    assert _details(recorder) == [{"count": 2}]
    owner.close()
    other.close()


def test_redis_snapshot_reads_and_publishes_in_one_script(
    server, clock, monkeypatch
) -> None:
    broker = _redis(server, clock)

    def refuse(*_args: object) -> None:
        raise AssertionError

    monkeypatch.setattr(broker, "publish", refuse)
    monkeypatch.setattr(broker, "broadcast", refuse)
    with broker.subscribe([TOPIC], timeout=0.01) as stream:
        broker.join(TOPIC, "a", {"id": 1}, 30)
        clock.advance(WINDOW + 0.01)
        broker.presence_schedule.run_due()
        message = _first(stream)

    assert not message.numbered
    assert message.envelope.ops[0].extras["detail"] == {
        "count": 1,
        "members": [{"id": 1}],
    }
    assert broker.positions([TOPIC])[TOPIC].seq == 0
    broker.close()


def test_redis_snapshot_publishes_without_a_change_state(server, clock) -> None:
    broker = _redis(server, clock)
    with capture_signals(message_published) as recorder:
        broker.announce_presence(TOPIC)

    assert _details(recorder) == [{"count": 0}]
    broker.close()


def test_redis_keys_extend_only_once_half_their_span_is_left(server, clock) -> None:
    broker = _redis(server, clock)
    key = broker.topic_key("presence", TOPIC)
    broker.join(TOPIC, "a", None, 30)

    assert broker._redis.pttl(key) == 60_000
    clock.advance(10)
    broker.touch(TOPIC, "a", 30)
    assert broker._redis.pttl(key) == 50_000
    clock.advance(25)
    broker.touch(TOPIC, "a", 30)
    assert broker._redis.pttl(key) == 60_000
    broker.close()


def test_redis_async_scripts_are_bound_once_per_loop(server, clock) -> None:
    broker = _redis(server, clock)

    async def scenario() -> None:
        first = broker._async_script(redis_module._JOIN_SCRIPT)
        await broker.ajoin(TOPIC, "a", None, 30)
        assert broker._async_script(redis_module._JOIN_SCRIPT) is first
        (_, scripts), *_ = broker._async_clients.values()
        assert set(scripts) == set(redis_module._SCRIPTS)
        await broker.aclose()

    asyncio.run(scenario())


def test_redis_leave_reports_a_refused_script_as_a_broker_failure(
    server, clock, monkeypatch
) -> None:
    broker = _redis(server, clock)
    failure = redis.ResponseError("READONLY")

    def refuse(**_kwargs: object) -> None:
        raise failure

    async def arefuse(**_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(broker, "_leave_script", refuse)
    with pytest.raises(BrokerConnectionError, match="READONLY"):
        broker.leave(TOPIC, "a")

    async def scenario() -> None:
        _, scripts = broker._async_client()
        loop = asyncio.get_running_loop()
        client = broker._async_clients[loop][0]
        broker._async_clients[loop] = (
            client,
            {**scripts, redis_module._LEAVE_SCRIPT: arefuse},
        )
        with pytest.raises(BrokerConnectionError, match="READONLY"):
            await broker.aleave(TOPIC, "a")

    asyncio.run(scenario())
    broker.close()


def test_redis_broadcast_reaches_subscribers_unnumbered(server, clock) -> None:
    broker = _redis(server, clock)
    with broker.subscribe([TOPIC], timeout=0.01) as stream:
        sent = broker.broadcast(TOPIC, refresh_envelope())
        received = _first(stream)

    assert received == sent
    assert not received.numbered
    assert broker.positions([TOPIC])[TOPIC].seq == 0
    broker.close()


def test_broker_without_a_fan_out_refuses_to_broadcast() -> None:
    broker = PlainBroker()

    with pytest.raises(NotImplementedError, match="PlainBroker has no unnumbered"):
        broker.broadcast(TOPIC, refresh_envelope())
    assert broker.positions([TOPIC])[TOPIC].seq == 0


def test_announce_broadcasts_a_custom_broker_snapshot() -> None:
    broker = _memory()
    broker.join(TOPIC, "a", None, 30)
    with (
        capture_signals(message_published) as recorder,
        broker.subscribe([TOPIC], timeout=0.01) as stream,
    ):
        announce(broker, TOPIC)
        message = next(stream)

    assert not message.numbered
    assert _details(recorder) == [{"count": 1}]


def test_failed_snapshot_is_queued_again_after_the_retry(caplog) -> None:
    now = [0.0]
    attempts: list[str] = []

    def flaky(topic: str) -> None:
        attempts.append(topic)
        if len(attempts) == 1:
            raise ConnectionError(topic)

    schedule = PresenceSchedule(flaky, clock=lambda: now[0], threaded=False, retry=5)
    schedule.schedule("room:1", 0)

    assert schedule.run_due() == 1
    assert schedule.pending() == ("room:1",)
    assert schedule.run_due() == 0
    now[0] = 5
    assert schedule.run_due() == 1
    assert schedule.pending() == ()
    assert attempts == ["room:1", "room:1"]
    assert "Could not publish the presence" in caplog.text


def test_failed_snapshot_is_not_queued_twice_or_after_close() -> None:
    def fail(topic: str) -> None:
        raise ConnectionError(topic)

    schedule = PresenceSchedule(fail, clock=lambda: 0.0, threaded=False, retry=5)
    schedule.schedule("room:1", 0)
    schedule.schedule("room:1", 0)

    assert schedule.run_due() == 2
    assert schedule.pending() == ("room:1",)
    schedule.close()
    schedule._again("room:1")
    assert schedule.pending() == ()


def test_closing_a_schedule_publishes_what_is_pending(caplog) -> None:
    published: list[str] = []

    def publish(topic: str) -> None:
        if topic == "room:bad":
            raise ConnectionError(topic)
        published.append(topic)

    schedule = PresenceSchedule(publish, clock=lambda: 0.0, threaded=False)
    schedule.schedule("room:2", 9)
    schedule.schedule("room:1", 5)
    schedule.schedule("room:bad", 7)
    schedule.schedule("room:1", 8)
    schedule.close()

    assert published == ["room:1", "room:2"]
    assert schedule.pending() == ()
    assert "Could not publish the presence of 'room:bad'" in caplog.text


def test_fork_hook_resets_every_broker_schedule(server, clock) -> None:
    memory = MemoryBroker()
    remote = FakeServerBroker(server)
    memory.presence_schedule.schedule(TOPIC, 60)
    remote.presence_schedule.schedule(TOPIC, 60)
    lock = memory.presence_schedule._condition

    roster_module._reset_schedules()

    for schedule in (memory.presence_schedule, remote.presence_schedule):
        assert schedule._thread is None
        assert schedule.pending() == ()
    assert memory.presence_schedule._condition is not lock
    memory.close()
    remote.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="the platform cannot fork")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
def test_forked_child_publishes_its_own_snapshots() -> None:
    broker = MemoryBroker(presence={"WINDOW": 0.05})
    broker.join("room:parent", "p", None, 30)
    broker.presence_schedule.schedule("room:parent", 60)
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        schedule = broker.presence_schedule
        state = [schedule._thread is None, schedule.pending() == ()]
        with broker.subscribe(["room:child"], timeout=5) as stream:
            broker.join("room:child", "c", None, 30)
            message = next(stream)
        state.append(message is not None)
        os.write(writer, json.dumps(state).encode())
        os._exit(0)
    os.close(writer)
    with os.fdopen(reader) as pipe:
        state = json.loads(pipe.read())
    os.waitpid(pid, 0)
    broker.close()

    assert state == [True, True, True]


class _FlakyBroker(MemoryBroker):
    def leave(self, topic: str, connection: str) -> None:
        if topic == "room:1":
            raise BrokerConnectionError(topic)
        super().leave(topic, connection)


def test_leave_logs_a_failed_topic_and_leaves_the_others(caplog) -> None:
    broker = _FlakyBroker(schedule=silent_schedule)
    members = {"room:1": None, "room:2": None}
    attendance = Attendance(broker, members, heartbeat=1)
    attendance.join()
    with caplog.at_level(logging.WARNING, logger="next.realtime.attendance"):
        attendance.leave()

    assert broker.presence("room:2") == Presence(0)
    assert "Could not leave the presence of 'room:1'" in caplog.text

    async def scenario() -> Presence:
        await attendance.ajoin()
        await attendance.aleave()
        return broker.presence("room:2")

    caplog.clear()
    assert asyncio.run(scenario()) == Presence(0)
    assert "Could not leave the presence of 'room:1'" in caplog.text


def test_encode_member_reads_any_mapping() -> None:
    member = MappingProxyType({"b": MappingProxyType({"c": 1}), "a": [1]})

    assert encode_member(member, 64) == '{"a":[1],"b":{"c":1}}'
    with pytest.raises(InvalidMemberError, match="JSON cannot carry"):
        encode_member({"tags": {"x"}}, 64)


@pytest.mark.parametrize(
    "envelope",
    [
        refresh_envelope(),
        Patches(None).refresh(zone="a").refresh(zone="b").envelope(),
        presence_envelope(Presence(2)),
    ],
    ids=["other-op", "two-ops", "no-members"],
)
def test_counted_leaves_a_message_without_members_as_it_is(envelope) -> None:
    message = Message(TOPIC, 0, envelope, 1.0)

    assert counted(message) is message


def test_counted_cuts_the_members_of_a_numbered_snapshot() -> None:
    snapshot = snapshot_message(TOPIC, Presence(2, ({"id": 1},)), 1.0)
    message = Message(TOPIC, 3, snapshot.envelope, 1.0, "e1")

    cut = counted(message)
    assert (cut.seq, cut.epoch) == (3, "e1")
    (op,) = cut.envelope.ops
    assert op.extras == {"name": EVENT, "detail": {"count": 2}}


def test_counted_cuts_the_members_of_each_presence_event_among_other_ops() -> None:
    named = presence_envelope(Presence(2, ({"id": 1},))).ops[0]
    extra = Patch(op="event", extras={"name": EVENT, "detail": {"count": 1, "x": 1}})
    other = refresh_envelope().ops[0]
    envelope = Envelope(version="v1", ops=(other, named, extra, named))
    message = Message(TOPIC, 4, envelope, 1.0, "e1")

    ops = counted(message).envelope.ops
    assert ops[0] is other
    assert ops[2] is extra
    assert [op.extras["detail"] for op in ops[1::2]] == [{"count": 2}] * 2


def test_counted_cuts_the_members_and_keeps_the_count() -> None:
    message = snapshot_message(TOPIC, Presence(2, ({"id": 1},)), 1.0)

    (op,) = counted(message).envelope.ops
    assert op.extras == {"name": EVENT, "detail": {"count": 2}}


def test_unnumbered_message_round_trips_without_a_position() -> None:
    message = Message(TOPIC, 0, refresh_envelope(), 1.5)
    data = message.as_dict()

    assert {"seq", "epoch"}.isdisjoint(data)
    assert Message.from_dict(data) == message
    assert not message.numbered


@pytest.mark.parametrize("option", [None, 0, 1.5, ["x"]])
def test_stream_refuses_a_presence_of_another_type(option: object) -> None:
    request = RequestFactory().get("/rooms/1/stream/")

    with pytest.raises(ImproperlyConfigured, match="presence= takes a bool"):
        TopicStream(request, TOPIC, presence=option)


def test_asgi_stream_refuses_presence_on_a_configured_broker_without_it(
    monkeypatch,
) -> None:
    request = AsyncRequestFactory().get("/rooms/1/stream/")
    backend = {"BACKEND": "tests.support.realtime.PlainBroker"}
    monkeypatch.setattr(broker_manager, "_backend", None)
    with (
        override_settings(NEXT_FRAMEWORK={"REALTIME": {"BROKER": backend}}),
        pytest.raises(ImproperlyConfigured, match="keeps no presence"),
    ):
        TopicStream(request, TOPIC, presence=True)
    assert broker_manager._backend is None
    monkeypatch.setattr(broker_manager, "_backend", PlainBroker())
    with pytest.raises(ImproperlyConfigured, match="keeps no presence"):
        TopicStream(request, TOPIC, presence=True)


@pytest.fixture()
def memory(monkeypatch) -> Iterator[MemoryBroker]:
    broker = _memory()
    monkeypatch.setattr(broker_manager, "_backend", broker)
    yield broker
    broker.close()


def _sync_stream(presence: object, **options: object) -> SseReader:
    request = RequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    stream = TopicStream(
        request, TOPIC, presence=presence, heartbeat_seconds=0.05, **options
    )
    events = SseReader(stream)
    assert next(events).event == "next-hello"
    return events


def _detail(event: SseEvent) -> object:
    (op,) = event.json()["ops"]
    assert op["name"] == EVENT
    return op["detail"]


@pytest.mark.django_db()
def test_sse_snapshot_after_hello_is_an_event_without_id_or_seq(memory) -> None:
    event = next(_sync_stream(True))

    assert event.event == "next-patches"
    assert event.id is None
    assert {"seq", "epoch"}.isdisjoint(event.json())
    assert event.json()["topic"] == TOPIC
    assert _detail(event) == {"count": 1}


@pytest.mark.django_db()
def test_sse_shows_members_only_to_an_identifying_stream(memory) -> None:
    counting = _sync_stream(True)
    assert _detail(next(counting)) == {"count": 1}
    naming = _sync_stream(f"{_HERE}.identify")
    assert _detail(next(naming)) == {"count": 2, "members": [identify(None, TOPIC)]}
    plain = _sync_stream(False)
    memory.presence_schedule.run_due(10)

    member = [identify(None, TOPIC)]
    assert _detail(next(counting)) == {"count": 2}
    assert _detail(next(naming)) == {"count": 2, "members": member}
    assert _detail(next(plain)) == {"count": 2}


@pytest.mark.django_db()
def test_sse_snapshot_skips_the_subscriber_transform(memory) -> None:
    iterator = _sync_stream(True, transform=skip_everything)

    assert _detail(next(iterator)) == {"count": 1}


def test_asgi_snapshot_after_hello_hides_the_members_of_a_count_stream(memory) -> None:
    memory.join(TOPIC, "other", identify(None, TOPIC), 30)
    request = AsyncRequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    stream = TopicStream(
        request, TOPIC, presence=True, transform=skip_everything, heartbeat_seconds=5
    )

    async def scenario() -> list[object]:
        source = stream._async_messages(memory)
        hello = await anext(source)
        snapshot = await anext(source)
        memory.presence_schedule.schedule(TOPIC, 0)
        memory.leave(TOPIC, "other")
        memory.presence_schedule.run_due(10)
        live = await anext(source)
        await source.aclose()
        return [hello, snapshot, live]

    _, snapshot, live = asyncio.run(scenario())
    assert isinstance(snapshot, Message)
    assert not snapshot.numbered
    assert snapshot.envelope.ops[0].extras["detail"] == {"count": 2}
    assert isinstance(live, Message)
    assert live.envelope.ops[0].extras["detail"] == {"count": 1}


async def _ws_open(port: MemoryPort, issued: SocketTicket, broker) -> asyncio.Task:
    handshake = Handshake(issued.credential, AnonymousUser(), None)
    socket = TopicSocket(port, handshake, broker=broker, heartbeat=60)
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(issued.subscribe_frame())
    async with asyncio.timeout(5):
        assert (await port.receive_json())["type"] == "hello"
    return task


async def _ws_detail(port: MemoryPort) -> object:
    async with asyncio.timeout(5):
        frame = await port.receive_json()
    assert frame["type"] == "patches"
    assert {"seq", "epoch"}.isdisjoint(frame["data"])
    (op,) = frame["data"]["ops"]
    return op["detail"]


@pytest.mark.django_db(transaction=True)
def test_websocket_shows_members_only_to_an_identifying_ticket() -> None:
    tickets = [
        issue_session_ticket((TOPIC,), presence=True),
        issue_session_ticket((TOPIC,), presence=f"{_HERE}.identify"),
        issue_session_ticket((TOPIC,), transform_ref=f"{_HERE}.skip_everything"),
    ]

    async def scenario() -> list[object]:
        broker = _memory()
        ports = [MemoryPort(), MemoryPort(), MemoryPort()]
        tasks = [await _ws_open(ports[0], tickets[0], broker)]
        first = await _ws_detail(ports[0])
        tasks.append(await _ws_open(ports[1], tickets[1], broker))
        second = await _ws_detail(ports[1])
        tasks.append(await _ws_open(ports[2], tickets[2], broker))
        broker.presence_schedule.run_due(10)
        live = [await _ws_detail(port) for port in ports]
        for port, task in zip(ports, tasks, strict=True):
            port.disconnect()
            async with asyncio.timeout(5):
                await task
        return [first, second, *live]

    member = [identify(None, TOPIC)]
    assert asyncio.run(scenario()) == [
        {"count": 1},
        {"count": 2, "members": member},
        {"count": 2},
        {"count": 2, "members": member},
        {"count": 2},
    ]


@pytest.mark.parametrize("malformed", ["not json", "[1]"])
def test_redis_snapshot_skips_a_malformed_member_and_keeps_the_others(
    server, clock, malformed: str, caplog
) -> None:
    broker = _redis(server, clock)
    broker.join(TOPIC, "a", {"id": 1}, 30)
    deadline = clock.now * 1000 + 30_000
    broker._redis.zadd(broker.topic_key("attendees", TOPIC), {malformed: deadline})
    broker._redis.zadd(broker.topic_key("order", TOPIC), {malformed: clock.now * 1000})

    assert broker.presence(TOPIC) == Presence(2, ({"id": 1},))
    assert "Skipped a malformed presence member" in caplog.text
    broker.close()


def _lasting(broker: FakeServerBroker) -> dict[str, int]:
    return {key: broker._redis.pttl(key) for key in broker._redis.scan_iter("next:*")}


def _expiring(broker: FakeServerBroker) -> None:
    lasting = _lasting(broker)
    assert lasting
    assert all(left > 0 for left in lasting.values()), lasting


def _unnamed(broker: FakeServerBroker, clock: ServerClock) -> None:
    broker.join(TOPIC, "a", None, 30)
    _expiring(broker)
    broker.leave(TOPIC, "a")


def _named(broker: FakeServerBroker, clock: ServerClock) -> None:
    broker.join(TOPIC, "a", {"id": 1}, 30)
    _expiring(broker)
    assert broker.touch(TOPIC, "a", 30)
    _expiring(broker)
    broker.leave(TOPIC, "a")


def _switching(broker: FakeServerBroker, clock: ServerClock) -> None:
    broker.join(TOPIC, "a", None, 30)
    broker.join(TOPIC, "a", {"id": 1}, 30)
    _expiring(broker)
    broker.join(TOPIC, "a", {"id": 2}, 30)
    broker.join(TOPIC, "a", None, 30)
    _expiring(broker)
    broker.leave(TOPIC, "a")


def _lapsing(broker: FakeServerBroker, clock: ServerClock) -> None:
    broker.join(TOPIC, "a", {"id": 1}, 1)
    broker.join(TOPIC, "b", None, 30)
    clock.advance(2)
    assert not broker.touch(TOPIC, "a", 30)
    _expiring(broker)
    broker.leave(TOPIC, "b")


def _persisted(broker: FakeServerBroker, clock: ServerClock) -> None:
    broker.join(TOPIC, "a", None, 30)
    broker._redis.persist(broker.topic_key("state", TOPIC))
    broker.leave(TOPIC, "a")


@pytest.mark.parametrize(
    "scenario", [_unnamed, _named, _switching, _lapsing, _persisted]
)
def test_redis_presence_keys_always_expire_and_vanish_once_the_topic_empties(
    server, clock, scenario: Callable[[FakeServerBroker, ServerClock], None]
) -> None:
    broker = _redis(server, clock)
    scenario(broker, clock)
    _expiring(broker)
    clock.advance(WINDOW + 0.01)
    broker.presence_schedule.run_due()
    _expiring(broker)

    clock.advance(120)
    assert _lasting(broker) == {}
    broker.close()


@pytest.mark.parametrize("step", range(1, 7))
def test_redis_snapshots_of_two_brokers_end_on_the_latest_change(
    server, clock, monkeypatch, step: int
) -> None:
    first, second, watcher = (_redis(server, clock) for _ in range(3))
    first.join(TOPIC, "a", {"id": 1}, 30)
    calls = [0]
    execute = first._redis.execute_command

    def interleaved(*args: object, **options: object) -> object:
        result = execute(*args, **options)
        calls[0] += 1
        if calls[0] == step:
            second.join(TOPIC, "b", {"id": 2}, 30)
            second.announce_presence(TOPIC)
        return result

    monkeypatch.setattr(first._redis, "execute_command", interleaved)
    with watcher.subscribe([TOPIC], timeout=0.01) as stream:
        first.announce_presence(TOPIC)
        received = _until_idle(stream)

    latest = received[-1].envelope.ops[0].extras["detail"]
    assert latest == watcher.presence(TOPIC).as_detail()
    for broker in (first, second, watcher):
        broker.close()


def test_last_leave_under_a_dead_window_announces_once_it_closes(server, clock) -> None:
    owner, other = _redis(server, clock), _redis(server, clock)
    owner.join(TOPIC, "a", None, 30)
    owner.leave(TOPIC, "a")
    owner.presence_schedule = PresenceSchedule(lambda _topic: None, threaded=False)
    other.join(TOPIC, "b", None, 30)
    other.join(TOPIC, "c", None, 30)
    other.leave(TOPIC, "b")
    assert other.presence_schedule.pending() == ()

    other.leave(TOPIC, "c")
    assert other.presence_schedule.pending() == (TOPIC,)
    with capture_signals(message_published) as recorder:
        assert other.presence_schedule.run_due() == 0
        clock.advance(WINDOW)
        assert other.presence_schedule.run_due() == 1
    assert _details(recorder) == [{"count": 0}]
    owner.close()
    other.close()


def test_last_leave_under_a_live_window_announces_the_change_once(
    server, clock
) -> None:
    owner, other = _redis(server, clock), _redis(server, clock)
    owner.join(TOPIC, "a", None, 30)
    other.join(TOPIC, "b", None, 30)
    owner.leave(TOPIC, "a")
    other.leave(TOPIC, "b")
    clock.advance(WINDOW)
    with capture_signals(message_published) as recorder:
        assert owner.presence_schedule.run_due() >= 1
        assert other.presence_schedule.run_due() == 1
    assert _details(recorder) == [{"count": 0}]
    owner.close()
    other.close()


@pytest.mark.django_db()
def test_sse_cuts_the_members_of_a_numbered_presence_event(memory) -> None:
    counting = _sync_stream(False)
    naming = _sync_stream(f"{_HERE}.identify")
    next(naming)
    envelope = presence_envelope(Presence(1, ({"id": 7},)))
    memory.publish(TOPIC, envelope)

    assert _detail(next(counting)) == {"count": 1}
    assert _detail(next(naming)) == {"count": 1, "members": [{"id": 7}]}
