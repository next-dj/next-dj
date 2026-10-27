import asyncio
import threading
import time
from contextlib import ExitStack

import pytest

from next.realtime import InvalidTopicError, MemoryBroker, Position
from next.realtime.backends import _AsyncSubscriber, _SyncSubscriber
from tests.support.realtime import (
    PlainBroker,
    ServerClock,
    anext_item,
    refresh_envelope,
)


def test_memory_publish_per_topic_numbers_sequences_and_epochs() -> None:
    backend = MemoryBroker()
    first = backend.publish("poll:1", refresh_envelope())
    second = backend.publish("poll:2", refresh_envelope())
    third = backend.publish("poll:1", refresh_envelope())

    assert (first.seq, second.seq, third.seq) == (1, 1, 2)
    assert first.epoch == third.epoch != second.epoch
    assert backend.positions(["poll:1", "poll:2", "poll:empty", "poll:1"]) == {
        "poll:1": Position(first.epoch, 2),
        "poll:2": Position(second.epoch, 1),
        "poll:empty": Position("", 0),
    }
    with backend.subscribe(["poll:1"], timeout=0.01) as messages:
        assert next(messages) is None
        backend.publish("poll:1", refresh_envelope())
        assert next(messages).seq == 3


def test_memory_sync_subscriber_overflow_keeps_newest_messages() -> None:
    backend = MemoryBroker({"OPTIONS": {"QUEUE_SIZE": 2}})
    with backend.subscribe(["poll:1"], timeout=0.01) as messages:
        for _ in range(3):
            backend.publish("poll:1", refresh_envelope())
        assert [next(messages).seq for _ in range(2)] == [2, 3]


def test_memory_fanout_thirty_bounded_subscribers_keep_newest() -> None:
    backend = MemoryBroker({"OPTIONS": {"QUEUE_SIZE": 1}})
    with ExitStack() as stack:
        subscribers = [
            stack.enter_context(backend.subscribe(["poll:1"], timeout=0.01))
            for _ in range(30)
        ]
        backend.publish("poll:1", refresh_envelope())
        backend.publish("poll:1", refresh_envelope())
        assert [next(messages).seq for messages in subscribers] == [2] * 30


def test_memory_subscribe_duplicate_topics_delivers_once_and_unregisters() -> None:
    backend = MemoryBroker()
    with backend.subscribe(["poll:1", "poll:1"], timeout=0.01) as messages:
        backend.publish("poll:1", refresh_envelope())
        assert next(messages).seq == 1
    assert backend._subscribers == {}


def test_memory_subscribe_invalid_topics_raise() -> None:
    backend = MemoryBroker()
    with pytest.raises(ValueError, match="at least one topic"):
        backend.subscribe([], timeout=0.01)
    with pytest.raises(InvalidTopicError):
        backend.subscribe(["next:reserved"], timeout=0.01)


def test_memory_publish_invalid_input_raises() -> None:
    backend = MemoryBroker()
    with pytest.raises(InvalidTopicError):
        backend.publish("bad topic", refresh_envelope())
    with pytest.raises(TypeError, match="Envelope"):
        backend.publish("poll:1", object())


@pytest.mark.parametrize("size", [0, -1, True, "2"])
def test_memory_queue_size_not_positive_integer_raises(size: object) -> None:
    with pytest.raises(ValueError, match="QUEUE_SIZE"):
        MemoryBroker({"OPTIONS": {"QUEUE_SIZE": size}})


def test_memory_async_subscriber_thread_publish_is_delivered_in_order() -> None:
    async def scenario() -> None:
        backend = MemoryBroker()
        async with backend.asubscribe(["poll:1"], timeout=0.1) as messages:
            publisher = threading.Thread(
                target=backend.publish, args=("poll:1", refresh_envelope())
            )
            publisher.start()
            async with asyncio.timeout(5):
                message = await anext_item(messages)
            publisher.join(timeout=1)
            assert message.seq == 1
            assert await backend.apositions(["poll:1"]) == {
                "poll:1": Position(message.epoch, 1)
            }
        assert backend._subscribers == {}

    asyncio.run(scenario())


def test_memory_async_subscriber_idle_yields_none() -> None:
    async def scenario() -> None:
        backend = MemoryBroker()
        async with backend.asubscribe(["poll:1"], timeout=0.001) as messages:
            assert await anext(messages) is None

    asyncio.run(scenario())


def test_async_subscriber_repeated_enqueue_coalesces_wakeups() -> None:
    async def scenario() -> None:
        subscriber = _AsyncSubscriber(asyncio.get_running_loop(), 4)
        subscriber._notify()
        assert not subscriber.wake.is_set()
        message = MemoryBroker().publish("poll:1", refresh_envelope())
        assert subscriber.enqueue(message)
        assert subscriber.enqueue(message)
        assert await subscriber.get(0.1) == message
        assert await subscriber.get(0.1) == message
        subscriber.wake.set()
        assert await subscriber.get(0.1) is None

    asyncio.run(scenario())


def test_async_subscriber_closed_loop_is_dropped_on_publish() -> None:
    loop = asyncio.new_event_loop()
    subscriber = _AsyncSubscriber(loop, 1)
    loop.close()
    assert not subscriber.enqueue(MemoryBroker().publish("poll:1", refresh_envelope()))

    backend = MemoryBroker()
    backend._register(["poll:1", "poll:2"], subscriber)
    backend._subscribers["poll:1"].add(_SyncSubscriber(1))
    backend.publish("poll:1", refresh_envelope())
    assert subscriber not in backend._subscribers["poll:1"]
    assert subscriber not in backend._subscribers.get("poll:2", set())
    assert len(backend._subscribers["poll:1"]) == 1
    assert "poll:2" not in backend._subscribers


def test_memory_unregister_unknown_topic_is_noop() -> None:
    backend = MemoryBroker()
    subscriber = _SyncSubscriber(1)
    backend._unregister(["poll:missing"], subscriber)
    assert backend._subscribers == {}


def test_memory_unregister_shared_topic_keeps_other_subscribers() -> None:
    backend = MemoryBroker()
    first = _SyncSubscriber(1)
    second = _SyncSubscriber(1)
    backend._register(["poll:1"], first)
    backend._register(["poll:1"], second)
    backend._unregister(["poll:1"], first)
    assert backend._subscribers["poll:1"] == {second}


def test_memory_asubscribe_empty_topics_raises() -> None:
    async def scenario() -> None:
        backend = MemoryBroker()
        with pytest.raises(ValueError, match="at least one topic"):
            backend.asubscribe([], timeout=0.01)

    asyncio.run(scenario())


def test_memory_rebuilt_counter_starts_new_epoch() -> None:
    first = MemoryBroker().publish("poll:1", refresh_envelope())
    second = MemoryBroker().publish("poll:1", refresh_envelope())
    assert first.seq == second.seq == 1
    assert first.epoch
    assert first.epoch != second.epoch


def test_memory_positions_invalid_topic_raises() -> None:
    with pytest.raises(InvalidTopicError):
        MemoryBroker().positions(["next:reserved"])


def test_validate_publication_bad_topic_or_envelope_raises() -> None:
    backend = MemoryBroker()
    envelope = refresh_envelope()
    assert backend.validate_publication("poll:1", envelope) is envelope
    with pytest.raises(InvalidTopicError):
        backend.validate_publication("bad topic", envelope)
    with pytest.raises(TypeError, match="Envelope"):
        backend.validate_publication("poll:1", object())


def test_backend_default_close_hooks_keep_broker_usable() -> None:
    backend = MemoryBroker()
    backend.close()
    asyncio.run(backend.aclose())
    with backend.subscribe(["poll:1"], timeout=0.01) as messages:
        published = backend.publish("poll:1", refresh_envelope())
        assert next(messages) == published


def test_memory_broker_times_expiry_on_a_monotonic_clock() -> None:
    broker = MemoryBroker()
    assert broker.clock is time.monotonic
    assert broker.presence_schedule._clock is time.monotonic
    broker.close()


def test_memory_broker_stamps_the_wall_clock_whatever_clock_it_times_on() -> None:
    clock = ServerClock(5.0)
    broker = MemoryBroker(clock=clock, replay={"SIZE": 10, "TTL": 5})
    before = time.time()
    first = broker.publish("poll:1", refresh_envelope())
    clock.advance(4.9)
    second = broker.broadcast("poll:1", refresh_envelope())
    assert before <= first.published_at <= second.published_at <= time.time()
    assert broker.replay("poll:1", Position(first.epoch, 0)).messages == (first,)
    clock.advance(0.1)
    assert broker.positions(["poll:1"])["poll:1"] == Position("", 0)
    broker.close()


class _ClosingBroker(PlainBroker):
    def __init__(self) -> None:
        super().__init__()
        self.closed_on_a_loop: list[bool] = []

    def close(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.closed_on_a_loop.append(False)
        else:
            self.closed_on_a_loop.append(True)


def test_backend_default_aclose_runs_close_off_the_event_loop() -> None:
    backend = _ClosingBroker()
    asyncio.run(backend.aclose())
    assert backend.closed_on_a_loop == [False]
