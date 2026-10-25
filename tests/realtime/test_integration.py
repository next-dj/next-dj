import asyncio
import multiprocessing
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
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
    RedisBroker,
    TopicStream,
)
from next.realtime.manager import broker_manager
from tests.realtime.publisher import TOPIC, WORKER_BATCH, publish_batch
from tests.support.realtime import ResumingClient, anext_frame, next_frame


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
) -> list[bytes]:
    """Read replay and hello frames, publish one live message, then read it."""
    if mode == "sync":
        iterator = stream.streaming_content
        next(iterator)
        frames = [next_frame(iterator) for _ in range(count)]
        broker.publish(TOPIC, _envelope())
        frames.append(next_frame(iterator))
        stream._iterator.close()
        return frames

    async def collect() -> list[bytes]:
        iterator = stream.streaming_content.__aiter__()
        await anext(iterator)
        frames = [await anext_frame(iterator) for _ in range(count)]
        await broker.apublish(TOPIC, _envelope())
        frames.append(await anext_frame(iterator))
        await stream._iterator.aclose()
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
