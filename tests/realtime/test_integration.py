import asyncio
import multiprocessing
import os
import signal
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
import redis
import redis.asyncio
from django.test import AsyncRequestFactory, RequestFactory
from redis.crc import key_slot

from next.partial import Envelope, Patches
from next.realtime import (
    BrokerConnectionError,
    Message,
    Position,
    Presence,
    RedisBroker,
    TopicStream,
)
from next.realtime.manager import broker_manager
from next.realtime.roster import EVENT
from next.testing import SseEvent, SseReader
from tests.realtime.attendee import CONNECTION, TOPIC as ROOM, attend_until_killed
from tests.realtime.publisher import TOPIC, WORKER_BATCH, publish_batch
from tests.support.realtime import ResumingClient, manual_schedule, silent_schedule


REDIS_URL = os.environ.get("REDIS_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_URL, reason="REDIS_URL points at no Redis server"),
]


def _envelope(worker: int = 0) -> Envelope:
    return Patches(None).event("poll:update", {"worker": worker}).envelope()


def _named_url(url: str, client_name: str) -> str:
    parts = urlsplit(url)
    query = [*parse_qsl(parts.query), ("client_name", client_name)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _connections(admin: redis.Redis, client_name: str) -> list[dict]:
    return [entry for entry in admin.client_list() if entry["name"] == client_name]


async def _aconnections(admin: redis.asyncio.Redis, client_name: str) -> list[dict]:
    clients = await admin.client_list()
    return [entry for entry in clients if entry["name"] == client_name]


def _wait_for(condition: Callable[[], object], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail("The awaited condition never held.")
        time.sleep(0.01)


def _wait_until_gone(admin: redis.Redis, client_name: str) -> list[dict]:
    deadline = time.monotonic() + 5
    remaining = _connections(admin, client_name)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.05)
        remaining = _connections(admin, client_name)
    return remaining


@pytest.fixture()
def admin() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield client
    client.close()


@pytest.fixture()
def prefix(admin: redis.Redis) -> Iterator[str]:
    namespace = f"it{uuid.uuid4().hex[:12]}"
    yield namespace
    stale = list(admin.scan_iter(match=f"{namespace}:*"))
    if stale:
        admin.delete(*stale)


@pytest.fixture()
def client_name() -> str:
    return f"nextdj-it-{uuid.uuid4().hex[:12]}"


@pytest.fixture()
def broker(prefix: str, client_name: str) -> Iterator[RedisBroker]:
    backend = RedisBroker(
        {"OPTIONS": {"URL": _named_url(REDIS_URL, client_name), "PREFIX": prefix}}
    )
    yield backend
    backend.close()


def _collect(messages: Iterator[Message | None], count: int) -> list[Message]:
    received: list[Message] = []
    deadline = time.monotonic() + 60
    while len(received) < count and time.monotonic() < deadline:
        message = next(messages)
        if message is not None:
            received.append(message)
    return received


def test_redis_sync_publish_right_after_subscribe_is_delivered(broker) -> None:
    with broker.subscribe([TOPIC], timeout=2) as messages:
        published = broker.publish(TOPIC, _envelope())
        assert next(messages) == published


def test_redis_async_publish_right_after_subscribe_is_delivered(broker) -> None:
    async def scenario() -> None:
        async with broker.asubscribe([TOPIC], timeout=2) as messages:
            published = await broker.apublish(TOPIC, _envelope())
            assert await anext(messages) == published

    asyncio.run(scenario())


def test_redis_two_processes_publishing_share_one_increasing_sequence(
    broker, prefix
) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = [
        context.Process(
            target=publish_batch, args=(REDIS_URL, prefix, _envelope(index), start)
        )
        for index in (1, 2)
    ]
    with broker.subscribe([TOPIC], timeout=1) as messages:
        for worker in workers:
            worker.start()
        start.set()
        received = _collect(messages, 2 * WORKER_BATCH)
    for worker in workers:
        worker.join(30)

    assert [worker.exitcode for worker in workers] == [0, 0]
    assert [message.seq for message in received] == list(range(1, 2 * WORKER_BATCH + 1))
    assert len({message.epoch for message in received}) == 1
    assert {
        message.envelope.ops[0].extras["detail"]["worker"] for message in received
    } == {1, 2}
    assert broker.positions([TOPIC]) == {
        TOPIC: Position(received[0].epoch, 2 * WORKER_BATCH)
    }


def test_redis_counter_lost_while_subscribed_starts_a_new_epoch(
    broker, admin, prefix
) -> None:
    with broker.subscribe([TOPIC], timeout=2) as messages:
        first = broker.publish(TOPIC, _envelope())
        assert next(messages) == first
        admin.delete(f"{prefix}:seq:{{{TOPIC}}}")
        restarted = broker.publish(TOPIC, _envelope())
        received = next(messages)

    assert received == restarted
    assert received.seq == 1
    assert received.epoch != first.epoch
    assert broker.positions([TOPIC]) == {TOPIC: Position(received.epoch, 1)}


def _kill_subscription(admin: redis.Redis, client_name: str) -> None:
    (connection,) = [
        entry
        for entry in _connections(admin, client_name)
        if int(entry["sub"]) or int(entry["psub"])
    ]
    assert admin.client_kill_filter(_id=connection["id"], _type="pubsub") == 1


def _drain(messages: Iterator[Message | None]) -> None:
    for _ in range(10):
        next(messages)


async def _drain_async(messages: AsyncIterator[Message | None]) -> None:
    for _ in range(10):
        await anext(messages)


def test_redis_sync_subscription_killed_by_server_raises_connection_error(
    broker, admin, client_name
) -> None:
    with broker.subscribe([TOPIC], timeout=0.2) as messages:
        _kill_subscription(admin, client_name)
        with pytest.raises(BrokerConnectionError):
            _drain(messages)


def test_redis_async_subscription_killed_by_server_raises_connection_error(
    broker, admin, client_name
) -> None:
    async def scenario() -> None:
        async with broker.asubscribe([TOPIC], timeout=0.2) as messages:
            _kill_subscription(admin, client_name)
            with pytest.raises(BrokerConnectionError):
                await _drain_async(messages)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_after", ["socket", "entry"])
def test_redis_async_subscription_cancelled_leaves_no_connection(
    broker, admin, client_name, cancel_after
) -> None:
    async def scenario() -> None:
        observer = redis.asyncio.Redis.from_url(REDIS_URL, decode_responses=True)
        entered = asyncio.Event()

        async def hold() -> None:
            async with broker.asubscribe([TOPIC], timeout=30) as messages:
                entered.set()
                await anext(messages)

        task = asyncio.create_task(hold())
        if cancel_after == "entry":
            await entered.wait()
        else:
            while not await _aconnections(observer, client_name):
                continue
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await observer.aclose()

    asyncio.run(scenario())
    assert _wait_until_gone(admin, client_name) == []


def test_redis_sync_subscription_left_by_error_leaves_no_connection(
    broker, admin, client_name
) -> None:
    def fail_inside() -> None:
        with broker.subscribe([TOPIC], timeout=30):
            assert len(_connections(admin, client_name)) == 1
            raise LookupError

    with pytest.raises(LookupError):
        fail_inside()
    assert _wait_until_gone(admin, client_name) == []


def test_redis_topic_keys_share_one_hash_slot(broker, admin, prefix) -> None:
    broker.publish(TOPIC, _envelope())
    keys = [broker.topic_key(kind, TOPIC) for kind in ("seq", "log")]
    assert keys == [f"{prefix}:seq:{{{TOPIC}}}", f"{prefix}:log:{{{TOPIC}}}"]
    assert admin.exists(*keys) == 2
    assert admin.type(keys[1]) == "list"
    assert 0 < admin.pttl(keys[1]) <= 300_000
    assert {key_slot(key.encode()) for key in keys} == {key_slot(TOPIC.encode())}


def _resumed_frames(
    stream: TopicStream, broker: RedisBroker, count: int, *, mode: str
) -> list[SseEvent]:
    """Read replay and hello events, publish one live message, then read it."""
    if mode == "sync":
        with SseReader(stream) as events:
            frames = [next(events) for _ in range(count)]
            broker.publish(TOPIC, _envelope())
            frames.append(next(events))
        return frames

    async def collect() -> list[SseEvent]:
        async with SseReader(stream) as events:
            frames = [await anext(events) for _ in range(count)]
            await broker.apublish(TOPIC, _envelope())
            frames.append(await anext(events))
        return frames

    return asyncio.run(collect())


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_redis_reconnect_with_a_cursor_replays_exactly_the_missed_messages(
    broker, admin, client_name, monkeypatch, mode
) -> None:
    monkeypatch.setattr(broker_manager, "_backend", broker)
    with broker.subscribe([TOPIC], timeout=0.2) as messages:
        seen = broker.publish(TOPIC, _envelope())
        assert next(messages) == seen
        _kill_subscription(admin, client_name)
        with pytest.raises(BrokerConnectionError):
            _drain(messages)
    missed = [broker.publish(TOPIC, _envelope()) for _ in range(3)]
    factory = AsyncRequestFactory() if mode == "async" else RequestFactory()
    request = factory.get(
        "/polls/1/stream/", headers={"Last-Event-ID": f"{seen.epoch}:{seen.seq}"}
    )
    stream = TopicStream(request, TOPIC, heartbeat_seconds=0.05)
    client = ResumingClient({TOPIC: (seen.epoch, seen.seq)})
    frames = _resumed_frames(stream, broker, len(missed) + 1, mode=mode)
    events = [client.sse(frame) for frame in frames]
    assert events == ["next-patches"] * 3 + ["next-hello", "next-patches"]
    assert client.applied == [
        (TOPIC, seen.epoch, seq) for seq in (*(m.seq for m in missed), 5)
    ]
    assert client.dropped == []
    assert client.gaps == []


def _presence_counts(broker: RedisBroker, messages: Iterator[Message | None]) -> list:
    """Touch the survivor on every idle tick and collect counts until one is left."""
    counts: list[object] = []
    deadline = time.monotonic() + 60
    while counts[-1:] != [1] and time.monotonic() < deadline:
        message = next(messages)
        if message is None:
            broker.touch(ROOM, "survivor", 30)
            continue
        (op,) = message.envelope.ops
        if op.extras.get("name") == EVENT:
            counts.append(op.extras["detail"]["count"])
    return counts


def test_redis_presence_of_a_killed_process_expires_and_is_announced(prefix) -> None:
    context = multiprocessing.get_context("spawn")
    joined = context.Event()
    attendee = context.Process(
        target=attend_until_killed, args=(REDIS_URL, prefix, 2.0, joined)
    )
    survivor = RedisBroker(
        {"OPTIONS": {"URL": REDIS_URL, "PREFIX": prefix}}, presence={"WINDOW": 0.1}
    )
    attendee.start()
    try:
        assert joined.wait(60)
        assert survivor.presence(ROOM) == Presence(1)
        with survivor.subscribe([ROOM], timeout=0.1) as messages:
            survivor.join(ROOM, "survivor", None, 30)
            attendee.kill()
            attendee.join(30)
            counts = _presence_counts(survivor, messages)
    finally:
        attendee.kill()
        attendee.join(30)
        survivor.close()

    assert attendee.exitcode == -signal.SIGKILL
    assert counts[-1:] == [1]
    assert survivor.presence(ROOM) == Presence(1)
    assert not survivor.touch(ROOM, CONNECTION, 30)


def _next_snapshot(messages: Iterator[Message | None]) -> Message:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        message = next(messages)
        if message is not None and message.envelope.ops[0].extras.get("name") == EVENT:
            return message
    pytest.fail("No presence snapshot arrived.")


def test_redis_snapshot_after_the_window_owner_died_arrives_on_a_touch(prefix) -> None:
    options = {"OPTIONS": {"URL": REDIS_URL, "PREFIX": prefix}}
    owner = RedisBroker(options, presence={"WINDOW": 0.2})
    other = RedisBroker(options, presence={"WINDOW": 0.2})
    try:
        with other.subscribe([ROOM], timeout=0.05) as messages:
            owner.presence_schedule.close()
            owner.join(ROOM, "owner", {"id": 2}, 30)
            other.join(ROOM, "other", {"id": 1}, 30)
            _wait_for(
                lambda: (
                    other.touch(ROOM, "other", 30) and other.presence_schedule.pending()
                )
            )
            snapshot = _next_snapshot(messages)
        position = other.positions([ROOM])[ROOM]
    finally:
        owner.close()
        other.close()

    assert not snapshot.numbered
    assert snapshot.envelope.ops[0].extras["detail"] == {
        "count": 2,
        "members": [{"id": 2}, {"id": 1}],
    }
    assert position.seq == 0


def _presence_broker(prefix: str, window: float = 0.05) -> RedisBroker:
    return RedisBroker(
        {"OPTIONS": {"URL": REDIS_URL, "PREFIX": prefix}},
        presence={"WINDOW": window},
        schedule=manual_schedule(time.monotonic),
    )


def _lasting(admin: redis.Redis, prefix: str) -> dict[str, int]:
    return {key: admin.pttl(key) for key in admin.scan_iter(match=f"{prefix}:*")}


def _assert_expiring(admin: redis.Redis, prefix: str) -> None:
    lasting = _lasting(admin, prefix)
    assert lasting
    assert all(0 < left <= 600 for left in lasting.values()), lasting


@pytest.mark.parametrize("member", [None, {"id": 1}], ids=["unnamed", "named"])
def test_redis_presence_keys_of_a_short_visit_expire(admin, prefix, member) -> None:
    broker = _presence_broker(prefix)
    try:
        broker.join(ROOM, "visitor", member, 0.3)
        _assert_expiring(admin, prefix)
        broker.leave(ROOM, "visitor")
        _wait_for(lambda: broker.presence_schedule.run_due() >= 1)
        _assert_expiring(admin, prefix)
        _wait_for(lambda: not _lasting(admin, prefix))
    finally:
        broker.close()

    assert _lasting(admin, prefix) == {}


@pytest.mark.parametrize("step", range(1, 5))
def test_redis_snapshots_of_two_brokers_end_on_the_latest_change(
    prefix, monkeypatch, step: int
) -> None:
    first, second, watcher = (_presence_broker(prefix, 1.0) for _ in range(3))
    first.join(ROOM, "a", {"id": 1}, 30)
    calls = [0]
    execute = first._redis.execute_command

    def interleaved(*args: object, **options: object) -> object:
        result = execute(*args, **options)
        calls[0] += 1
        if calls[0] == step:
            second.join(ROOM, "b", {"id": 2}, 30)
            second.announce_presence(ROOM)
        return result

    monkeypatch.setattr(first._redis, "execute_command", interleaved)
    try:
        with watcher.subscribe([ROOM], timeout=0.2) as messages:
            first.announce_presence(ROOM)
            details = []
            for message in messages:
                if message is None:
                    break
                details.append(message.envelope.ops[0].extras["detail"])
        latest = watcher.presence(ROOM).as_detail()
    finally:
        for broker in (first, second, watcher):
            broker.close()

    assert details
    assert details[-1] == latest


def test_redis_last_leave_under_a_dead_window_is_announced(prefix) -> None:
    owner, other = _presence_broker(prefix, 0.2), _presence_broker(prefix, 0.2)
    owner.presence_schedule = silent_schedule(owner.announce_presence)
    try:
        with other.subscribe([ROOM], timeout=0.05) as messages:
            owner.join(ROOM, "owner", None, 30)
            owner.leave(ROOM, "owner")
            other.join(ROOM, "other", None, 30)
            other.leave(ROOM, "other")
            assert other.presence_schedule.pending() == (ROOM,)
            _wait_for(lambda: other.presence_schedule.run_due() == 1)
            snapshot = _next_snapshot(messages)
    finally:
        owner.close()
        other.close()

    assert snapshot.envelope.ops[0].extras["detail"] == {"count": 0}
