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

from next.partial import Envelope, Patches
from next.realtime import BrokerConnectionError, Message, Position, RedisBroker


REDIS_URL = os.environ.get("REDIS_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_URL, reason="REDIS_URL points at no Redis server"),
]

_TOPIC = "poll:1"
_WORKER_BATCH = 25


def _envelope(worker: int = 0) -> Envelope:
    return Patches(None).event("poll:update", {"worker": worker}).envelope()


def _named_url(url: str, client_name: str) -> str:
    parts = urlsplit(url)
    query = [*parse_qsl(parts.query), ("client_name", client_name)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _publish_batch(url: str, prefix: str, envelope: Envelope, start) -> None:
    backend = RedisBroker({"OPTIONS": {"URL": url, "PREFIX": prefix}})
    start.wait(30)
    for _ in range(_WORKER_BATCH):
        backend.publish(_TOPIC, envelope)
    backend.close()


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
    with broker.subscribe([_TOPIC], timeout=2) as messages:
        published = broker.publish(_TOPIC, _envelope())
        assert next(messages) == published


def test_redis_async_publish_right_after_subscribe_is_delivered(broker) -> None:
    async def scenario() -> None:
        async with broker.asubscribe([_TOPIC], timeout=2) as messages:
            published = await broker.apublish(_TOPIC, _envelope())
            assert await anext(messages) == published

    asyncio.run(scenario())


def test_redis_two_processes_publishing_share_one_increasing_sequence(
    broker, prefix
) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = [
        context.Process(
            target=_publish_batch, args=(REDIS_URL, prefix, _envelope(index), start)
        )
        for index in (1, 2)
    ]
    with broker.subscribe([_TOPIC], timeout=1) as messages:
        for worker in workers:
            worker.start()
        start.set()
        received = _collect(messages, 2 * _WORKER_BATCH)
    for worker in workers:
        worker.join(30)

    assert [worker.exitcode for worker in workers] == [0, 0]
    assert [message.seq for message in received] == list(
        range(1, 2 * _WORKER_BATCH + 1)
    )
    assert len({message.epoch for message in received}) == 1
    assert {
        message.envelope.ops[0].extras["detail"]["worker"] for message in received
    } == {1, 2}
    assert broker.positions([_TOPIC]) == {
        _TOPIC: Position(received[0].epoch, 2 * _WORKER_BATCH)
    }


def test_redis_counter_lost_while_subscribed_starts_a_new_epoch(
    broker, admin, prefix
) -> None:
    with broker.subscribe([_TOPIC], timeout=2) as messages:
        first = broker.publish(_TOPIC, _envelope())
        assert next(messages) == first
        admin.delete(f"{prefix}:seq:{_TOPIC}")
        restarted = broker.publish(_TOPIC, _envelope())
        received = next(messages)

    assert received == restarted
    assert received.seq == 1
    assert received.epoch != first.epoch
    assert broker.positions([_TOPIC]) == {_TOPIC: Position(received.epoch, 1)}


def _kill_subscription(admin: redis.Redis, client_name: str) -> None:
    (connection,) = _connections(admin, client_name)
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
    with broker.subscribe([_TOPIC], timeout=0.2) as messages:
        _kill_subscription(admin, client_name)
        with pytest.raises(BrokerConnectionError):
            _drain(messages)


def test_redis_async_subscription_killed_by_server_raises_connection_error(
    broker, admin, client_name
) -> None:
    async def scenario() -> None:
        async with broker.asubscribe([_TOPIC], timeout=0.2) as messages:
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
            async with broker.asubscribe([_TOPIC], timeout=30) as messages:
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
        with broker.subscribe([_TOPIC], timeout=30):
            assert len(_connections(admin, client_name)) == 1
            raise LookupError

    with pytest.raises(LookupError):
        fail_inside()
    assert _wait_until_gone(admin, client_name) == []
