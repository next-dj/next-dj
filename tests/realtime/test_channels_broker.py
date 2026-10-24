import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest
import redis
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings


pytest.importorskip("channels")

from channels import layers as channel_layers
from channels.layers import InMemoryChannelLayer

from next.contrib.channels import broker as broker_module
from next.contrib.channels.broker import ChannelsLayerBroker
from next.realtime import BrokerConnectionError, Position
from next.realtime.errors import InvalidTopicError
from tests.support.realtime import event_envelope


@pytest.fixture()
def broker():
    with override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    ):
        cache.clear()
        backend = ChannelsLayerBroker(
            {"OPTIONS": {"PREFIX": "rt-test", "CACHE_ALIAS": "default"}}
        )
        yield backend
        backend.close()
        cache.clear()


def test_channels_sync_subscription_receives_own_topic_then_times_out(broker) -> None:
    with broker.subscribe(("room:a",), timeout=0.01) as messages:
        published = broker.publish("room:a", event_envelope())
        received = next(messages)
        assert received == published
        assert next(messages) is None
    assert broker.positions(["room:a", "room:b"]) == {
        "room:a": Position(published.epoch, 1),
        "room:b": Position("", 0),
    }


def test_channels_async_publish_reaches_multi_topic_subscription(broker) -> None:
    async def scenario() -> None:
        async with broker.asubscribe(("room:a", "room:b"), timeout=0.1) as messages:
            published = await broker.apublish("room:b", event_envelope("async"))
            received = await anext(messages)
            assert received == published
            positions = await broker.apositions(["room:b", "room:a"])
        assert positions == {
            "room:b": Position(published.epoch, 1),
            "room:a": Position("", 0),
        }

    asyncio.run(scenario())


def test_channels_async_idle_subscription_yields_none(broker) -> None:
    async def scenario() -> None:
        async with broker.asubscribe(("room:a",), timeout=0.001) as messages:
            assert await anext(messages) is None

    asyncio.run(scenario())


def test_channels_async_subscription_renews_group_before_expiry(
    broker, monkeypatch
) -> None:
    broker._layer.group_expiry = 0.05
    broker._group_renewal_seconds = 0.025
    original = broker._layer.group_add
    additions = 0

    async def observed_group_add(group: str, channel: str) -> None:
        nonlocal additions
        additions += 1
        await original(group, channel)

    monkeypatch.setattr(broker._layer, "group_add", observed_group_add)

    async def scenario() -> None:
        async with broker.asubscribe(("room:renew",), timeout=1) as messages:
            assert await anext(messages) is None
            assert additions >= 2

    asyncio.run(scenario())


def test_channels_bad_input_raises_and_unrelated_events_decode_to_none(broker) -> None:
    with pytest.raises(InvalidTopicError):
        broker.publish("next:reserved", event_envelope())
    with pytest.raises(TypeError, match="Envelope"):
        broker.publish("room", object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one topic"):
        broker.subscribe((), timeout=0.01)
    assert broker._decode({"type": "other"}) is None
    assert broker._decode({"type": "next.message", "payload": []}) is None
    assert broker._decode({"type": "next.message", "payload": {"seq": 1}}) is None


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("CHANNEL_LAYER", "", "CHANNEL_LAYER"),
        ("CACHE_ALIAS", "", "CACHE_ALIAS"),
        ("PREFIX", "unsafe prefix", "PREFIX"),
    ],
)
def test_channels_invalid_configuration_raises(option, value, message) -> None:
    with pytest.raises(ImproperlyConfigured, match=message):
        ChannelsLayerBroker({"OPTIONS": {option: value}})


def test_channels_missing_dependency_names_the_optional_extra(monkeypatch) -> None:
    # A None entry in sys.modules makes the import fail as if channels were absent.
    monkeypatch.setitem(sys.modules, "channels.layers", None)
    with pytest.raises(ImproperlyConfigured, match=r"next\.dj\[channels\]"):
        ChannelsLayerBroker()


def test_channels_missing_channel_layer_raises(monkeypatch) -> None:
    monkeypatch.setattr(channel_layers, "get_channel_layer", lambda _alias: None)
    with pytest.raises(ImproperlyConfigured, match="No Channels layer"):
        ChannelsLayerBroker()


@pytest.mark.parametrize("expiry", [None, True, 0, "invalid"])
def test_channels_invalid_group_expiry_falls_back_to_hourly_renewal(
    monkeypatch, expiry
) -> None:
    layer = create_autospec(InMemoryChannelLayer, instance=True)
    layer.group_expiry = expiry
    monkeypatch.setattr(channel_layers, "get_channel_layer", lambda _alias: layer)
    broker = ChannelsLayerBroker()
    assert broker._group_renewal_seconds == 3600


def test_channels_sync_subscription_renews_group_before_expiry(broker) -> None:
    broker._group_renewal_seconds = 0
    original = broker._layer.group_add
    additions = 0

    async def observed_group_add(group: str, channel: str) -> None:
        nonlocal additions
        additions += 1
        await original(group, channel)

    broker._layer.group_add = observed_group_add
    with broker.subscribe(("room:sync-renew",), timeout=0.01) as messages:
        assert next(messages) is None
        messages.close()
    assert additions >= 2


def test_channels_concurrent_sync_subscriptions_share_one_receive_loop(broker) -> None:
    original = broker._layer.receive
    loops = set()

    async def observed_receive(channel: str):
        loops.add(asyncio.get_running_loop())
        return await original(channel)

    broker._layer.receive = observed_receive
    barrier = threading.Barrier(4)
    received = []

    def subscriber(topic: str) -> None:
        with broker.subscribe((topic,), timeout=2) as messages:
            barrier.wait(5)
            received.append(next(messages))

    workers = [
        threading.Thread(target=subscriber, args=(f"room:{index}",), daemon=True)
        for index in range(3)
    ]
    for worker in workers:
        worker.start()
    barrier.wait(5)
    published = [
        broker.publish(f"room:{index}", event_envelope()) for index in range(3)
    ]
    for worker in workers:
        worker.join(5)
    assert sorted(received, key=lambda message: message.topic) == published
    assert len(loops) == 1
    assert broker._bridge._thread is not None
    assert broker._bridge._thread.is_alive()


def test_channels_close_stops_bridge_and_ends_sync_subscriptions(broker) -> None:
    with broker.subscribe(("room:a",), timeout=5) as messages:
        thread = broker._bridge._thread
        assert thread is not None
        broker.close()
        assert list(messages) == []
    assert not thread.is_alive()
    assert broker._bridge._thread is None
    broker.close()


def test_channels_sync_subscription_failed_receive_reraises(broker) -> None:
    async def failing_receive(channel: str):
        raise ConnectionError(channel)

    broker._layer.receive = failing_receive
    with (
        broker.subscribe(("room:a",), timeout=5) as messages,
        pytest.raises(BrokerConnectionError, match="receive failed"),
    ):
        next(messages)


def test_channels_sync_inbox_full_drops_oldest_message(broker, monkeypatch) -> None:
    monkeypatch.setattr(broker_module, "_SYNC_QUEUE_SIZE", 2)
    with broker.subscribe(("room:a",), timeout=0.5) as messages:
        published = [broker.publish("room:a", event_envelope()) for _ in range(3)]
        deadline = threading.Event()
        deadline.wait(0.1)
        assert [next(messages), next(messages)] == published[1:]


def test_channels_failed_group_join_still_discards_groups(broker) -> None:
    discarded = []

    async def failing_add(group: str, channel: str) -> None:
        raise ConnectionError(group)

    async def observed_discard(group: str, channel: str) -> None:
        discarded.append(group)

    broker._layer.group_add = failing_add
    broker._layer.group_discard = observed_discard
    with (
        pytest.raises(BrokerConnectionError),
        broker.subscribe(("room:a",), timeout=0.01),
    ):
        pytest.fail("A failed subscription cannot enter the context")
    assert discarded == [broker._group("room:a")]


def test_channels_close_with_stuck_thread_keeps_loop_open(broker) -> None:
    broker.publish("room:a", event_envelope())
    bridge = broker._bridge
    loop, thread = bridge._loop, bridge._thread
    bridge._thread = Mock(spec=threading.Thread)
    bridge._thread.is_alive.return_value = True
    broker.close()
    assert not loop.is_closed()
    thread.join(5)
    loop.close()


def _counter(broker, topic: str, epoch: str):
    return cache.get(broker._counter_key(topic, epoch))


def test_channels_lost_epoch_or_counter_starts_a_new_epoch(broker) -> None:
    first = broker.publish("room:a", event_envelope())
    second = broker.publish("room:a", event_envelope())
    cache.delete(broker._epoch_key("room:a"))
    fresh = broker.publish("room:a", event_envelope())
    cache.delete(broker._counter_key("room:a", fresh.epoch))
    rotated = broker.publish("room:a", event_envelope())
    following = broker.publish("room:a", event_envelope())

    assert first.epoch == second.epoch
    assert len(first.epoch) == 16
    assert (second.seq, fresh.seq, rotated.seq, following.seq) == (2, 1, 1, 2)
    assert len({first.epoch, fresh.epoch, rotated.epoch}) == 3
    assert following.epoch == rotated.epoch
    assert broker.positions(["room:a"]) == {"room:a": Position(rotated.epoch, 2)}


def test_channels_racing_first_publishers_share_the_winning_epoch(
    broker, monkeypatch
) -> None:
    original = broker._cache.add
    epoch_key = broker._epoch_key("room:a")
    raced = []
    fired = []

    def interleaved_add(key, value, timeout=None):
        if key == epoch_key and not fired:
            fired.append(key)
            raced.append(broker.publish("room:a", event_envelope()))
        return original(key, value, timeout=timeout)

    monkeypatch.setattr(broker._cache, "add", interleaved_add)
    late = broker.publish("room:a", event_envelope())

    (early,) = raced
    assert (early.epoch, early.seq) == (late.epoch, 1)
    assert late.seq == 2
    assert cache.get(epoch_key) == early.epoch


def test_channels_racing_rotations_agree_on_one_successor(broker, monkeypatch) -> None:
    lost = broker.publish("room:a", event_envelope())
    cache.delete(broker._counter_key("room:a", lost.epoch))
    original = broker._cache.add
    handoff = broker._handoff_key("room:a", lost.epoch)
    raced = []
    fired = []

    def interleaved_add(key, value, timeout=None):
        if key == handoff and not fired:
            fired.append(key)
            raced.append(broker.publish("room:a", event_envelope()))
        return original(key, value, timeout=timeout)

    monkeypatch.setattr(broker._cache, "add", interleaved_add)
    late = broker.publish("room:a", event_envelope())

    (early,) = raced
    assert early.epoch == late.epoch != lost.epoch
    assert (early.seq, late.seq) == (1, 2)


def test_channels_concurrent_publishers_never_reuse_a_position(broker) -> None:
    barrier = threading.Barrier(6)
    published = []

    def publisher() -> None:
        barrier.wait(5)
        published.extend(
            broker.publish("room:race", event_envelope()) for _ in range(40)
        )

    workers = [threading.Thread(target=publisher, daemon=True) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    pairs = [(message.epoch, message.seq) for message in published]
    assert len(pairs) == 240
    assert len(set(pairs)) == 240
    by_epoch: dict[str, list[int]] = {}
    for epoch, seq in pairs:
        by_epoch.setdefault(epoch, []).append(seq)
    for seqs in by_epoch.values():
        assert sorted(seqs) == list(range(1, len(seqs) + 1))


def test_channels_counter_that_keeps_vanishing_raises(broker, monkeypatch) -> None:
    def missing(_key):
        raise ValueError

    monkeypatch.setattr(broker._cache, "incr", missing)
    with pytest.raises(BrokerConnectionError, match="keeps losing"):
        broker.publish("room:a", event_envelope())


def test_channels_vanished_winning_epoch_raises(broker, monkeypatch) -> None:
    monkeypatch.setattr(
        broker._cache, "add", create_autospec(broker._cache.add, return_value=False)
    )
    with pytest.raises(BrokerConnectionError, match="dropped"):
        broker.publish("room:a", event_envelope())


def test_channels_positions_use_two_batched_reads(broker, monkeypatch) -> None:
    published = broker.publish("room:a", event_envelope())
    calls = []
    original = broker._cache.get_many

    def spy(keys):
        calls.append(list(keys))
        return original(keys)

    monkeypatch.setattr(broker._cache, "get_many", spy)
    positions = broker.positions(["room:a", "room:b", "room:c"])
    assert [len(keys) for keys in calls] == [3, 1]
    assert positions == {
        "room:a": Position(published.epoch, 1),
        "room:b": Position("", 0),
        "room:c": Position("", 0),
    }


def test_channels_positions_corrupt_cache_values_read_as_empty(broker) -> None:
    cache.set(broker._epoch_key("room:a"), "e1")
    cache.set(broker._counter_key("room:a", "e1"), "seven")
    cache.set(broker._epoch_key("room:b"), 9)
    cache.set(broker._epoch_key("room:c"), "e3")
    cache.set(broker._counter_key("room:c", "e3"), 0)
    assert broker.positions(["room:a", "room:b", "room:c"]) == {
        "room:a": Position("e1", 0),
        "room:b": Position("", 0),
        "room:c": Position("e3", 0),
    }


def test_channels_positions_keep_an_epoch_whose_counter_is_gone(broker) -> None:
    published = broker.publish("room:a", event_envelope())
    cache.delete(broker._counter_key("room:a", published.epoch))
    assert broker.positions(["room:a"]) == {"room:a": Position(published.epoch, 0)}


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("layer down"),
        OSError("socket"),
        redis.exceptions.ConnectionError("redis down"),
        redis.exceptions.TimeoutError("redis slow"),
    ],
)
def test_channels_layer_failures_surface_as_broker_errors(broker, failure) -> None:
    async def failing(*args, **kwargs):
        raise failure

    broker._layer.group_send = failing
    with pytest.raises(BrokerConnectionError) as caught:
        broker.publish("room:a", event_envelope())
    assert caught.value.__cause__ is failure

    async def scenario() -> None:
        with pytest.raises(BrokerConnectionError):
            await broker.apublish("room:a", event_envelope())
        broker._layer.receive = failing
        async with broker.asubscribe(("room:a",), timeout=1) as messages:
            with pytest.raises(BrokerConnectionError, match="receive failed"):
                await anext(messages)
        broker._layer.new_channel = failing
        with pytest.raises(BrokerConnectionError):
            async with broker.asubscribe(("room:a",), timeout=1):
                pytest.fail("A failed subscription cannot enter the context")

    asyncio.run(scenario())


def test_channels_cache_failures_surface_as_broker_errors(broker, monkeypatch) -> None:
    failure = redis.exceptions.ConnectionError("cache down")
    monkeypatch.setattr(
        broker._cache, "get", create_autospec(broker._cache.get, side_effect=failure)
    )
    monkeypatch.setattr(
        broker._cache,
        "get_many",
        create_autospec(broker._cache.get_many, side_effect=failure),
    )
    with pytest.raises(BrokerConnectionError, match="cache down"):
        broker.publish("room:a", event_envelope())
    with pytest.raises(BrokerConnectionError, match="cache down"):
        broker.positions(["room:a"])


def test_channels_unrelated_errors_are_not_translated(broker) -> None:
    async def failing(*args, **kwargs):
        raise RuntimeError

    broker._layer.group_send = failing
    with pytest.raises(RuntimeError):
        broker.publish("room:a", event_envelope())


def test_channels_transport_errors_include_installed_memcached_clients(
    monkeypatch,
) -> None:
    pymemcache_error = type("MemcacheError", (Exception,), {})
    pylibmc_error = type("Error", (Exception,), {})
    monkeypatch.setitem(
        sys.modules,
        "pymemcache.exceptions",
        SimpleNamespace(MemcacheError=pymemcache_error),
    )
    monkeypatch.setitem(sys.modules, "pylibmc", SimpleNamespace(Error=pylibmc_error))
    errors = broker_module._transport_errors()
    assert OSError in errors
    assert redis.exceptions.ConnectionError in errors
    assert pymemcache_error in errors
    assert pylibmc_error in errors


def test_channels_memcached_failures_surface_as_broker_errors(
    broker, monkeypatch
) -> None:
    memcache_error = type("MemcacheError", (Exception,), {})
    monkeypatch.setattr(
        broker_module,
        "_LAYER_FAILURES",
        (*broker_module._LAYER_FAILURES, memcache_error),
    )
    monkeypatch.setattr(broker._cache, "get", Mock(side_effect=memcache_error("down")))
    with pytest.raises(BrokerConnectionError, match="down"):
        broker.publish("room:a", event_envelope())


def test_channels_optional_errors_skip_a_missing_module() -> None:
    assert broker_module._optional_errors("next_missing_module", "Error") == ()
    assert broker_module._optional_errors("redis.exceptions", "ConnectionError") == (
        redis.exceptions.ConnectionError,
    )


def test_channels_async_cache_work_runs_detached(broker, monkeypatch) -> None:
    detached = []
    original = broker_module.run_detached

    async def spy(func, *args, **kwargs):
        detached.append(func.__name__)
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(broker_module, "run_detached", spy)

    async def scenario() -> None:
        await broker.apublish("room:a", event_envelope())
        await broker.apositions(["room:a"])

    asyncio.run(scenario())
    assert detached == ["_allocate", "positions"]


def test_channels_cleanup_after_close_is_dropped(broker) -> None:
    async def never_awaited() -> None:
        pytest.fail("cleanup ran on a closed bridge")

    broker.publish("room:a", event_envelope())
    broker.close()
    coroutine = never_awaited()
    broker._bridge.run_if_open(coroutine)
    assert coroutine.cr_frame is None
    assert broker._bridge._thread is None


def test_channels_slow_cleanup_is_bounded_and_logged(
    broker, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(broker_module, "_SHUTDOWN_TIMEOUT", 0.05)

    async def slow() -> None:
        await asyncio.sleep(5)

    broker.publish("room:a", event_envelope())
    started = time.monotonic()
    broker._bridge.run_if_open(slow())
    assert time.monotonic() - started < 2
    assert "cleanup did not finish" in caplog.text


def test_channels_close_with_stubborn_task_logs_and_frees_waiters(
    broker, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(broker_module, "_SHUTDOWN_TIMEOUT", 0.1)
    started = threading.Event()

    async def stubborn() -> None:
        started.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    errors = []

    def waiter() -> None:
        try:
            broker._bridge.run(stubborn())
        except BrokerConnectionError as exc:
            errors.append(exc)

    worker = threading.Thread(target=waiter, daemon=True)
    worker.start()
    assert started.wait(5)
    broker.close()
    worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert "ignored cancellation" in caplog.text


def test_channels_bridge_restarts_in_a_forked_child(broker) -> None:
    broker.publish("room:a", event_envelope())
    bridge = broker._bridge
    parent_loop, parent_thread = bridge._loop, bridge._thread
    parent_lock = bridge._lock
    broker_module._forget_bridges()
    assert (bridge._loop, bridge._thread) == (None, None)
    published = broker.publish("room:a", event_envelope())
    assert published.seq == 2
    assert bridge._loop is not parent_loop
    assert bridge._lock is not parent_lock
    parent_loop.call_soon_threadsafe(parent_loop.stop)
    parent_thread.join(5)
    parent_loop.close()


def test_channels_fork_hook_stand_in_accepts_hooks() -> None:
    assert broker_module._without_fork(after_in_child=lambda: None) is None


@pytest.mark.skipif(sys.platform != "linux", reason="fork with threads is Linux-safe")
def test_channels_real_fork_child_publishes_on_its_own_bridge(broker) -> None:
    broker.publish("room:a", event_envelope())
    pid = os.fork()
    if pid == 0:
        ok = broker._bridge._loop is None
        ok = ok and broker.publish("room:a", event_envelope()).seq == 2
        ok = ok and broker._bridge._thread is not None
        os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
