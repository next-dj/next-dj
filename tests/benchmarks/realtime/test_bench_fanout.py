import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, ExitStack

import pytest

from next.realtime.backends import MemoryBroker
from next.realtime.messages import Message
from next.realtime.redis.pubsub import decode_message
from tests.benchmarks.factories import medium_envelope, small_envelope


class TestBenchFanout:
    """One publish reaching many sync subscribers of the same topic."""

    @pytest.mark.parametrize("subscribers", [1, 100, 1000])
    @pytest.mark.benchmark(group="realtime.fanout")
    def test_memory_broker(
        self, memory_broker: MemoryBroker, subscribers: int, benchmark
    ) -> None:
        """Bounded queues drop their oldest message, so the steady state holds."""
        envelope = small_envelope()
        with ExitStack() as stack:
            streams = [
                stack.enter_context(memory_broker.subscribe(["room:1"], timeout=0.0))
                for _ in range(subscribers)
            ]
            benchmark(memory_broker.publish, "room:1", envelope)
            assert all(next(stream) is not None for stream in streams)


async def _subscribed(
    broker: MemoryBroker, stack: AsyncExitStack, count: int
) -> list[AsyncIterator[Message | None]]:
    return [
        await stack.enter_async_context(broker.asubscribe(["room:1"], timeout=1.0))
        for _ in range(count)
    ]


async def _first(streams: list[AsyncIterator[Message | None]]) -> list[Message | None]:
    return [await anext(stream) for stream in streams]


async def _closed(
    stack: AsyncExitStack, streams: list[AsyncIterator[Message | None]]
) -> None:
    for stream in streams:
        await stream.aclose()
    await stack.aclose()


class TestBenchAsyncFanout:
    """One sync publish reaching many async subscribers that live on one event loop."""

    @pytest.mark.parametrize("subscribers", [1, 100, 1000])
    @pytest.mark.benchmark(group="realtime.fanout")
    def test_wakeup_per_subscriber(
        self,
        memory_broker: MemoryBroker,
        bench_loop: asyncio.AbstractEventLoop,
        subscribers: int,
        benchmark,
    ) -> None:
        """Each round runs the pending wakeups first, so every enqueue schedules one."""
        envelope = small_envelope()
        stack = AsyncExitStack()
        streams = bench_loop.run_until_complete(
            _subscribed(memory_broker, stack, subscribers)
        )

        def drain() -> tuple[tuple[object, ...], dict[str, object]]:
            bench_loop.run_until_complete(asyncio.sleep(0))
            return ("room:1", envelope), {}

        try:
            benchmark.pedantic(
                memory_broker.publish, setup=drain, rounds=200, warmup_rounds=5
            )
            drain()
            received = bench_loop.run_until_complete(_first(streams))
            assert all(message is not None for message in received)
        finally:
            bench_loop.run_until_complete(_closed(stack, streams))

    @pytest.mark.parametrize("subscribers", [1, 100, 1000])
    @pytest.mark.benchmark(group="realtime.fanout")
    def test_coalesced_wakeup(
        self,
        memory_broker: MemoryBroker,
        bench_loop: asyncio.AbstractEventLoop,
        subscribers: int,
        benchmark,
    ) -> None:
        """The loop never runs between rounds, so each enqueue finds a wakeup queued."""
        envelope = small_envelope()
        stack = AsyncExitStack()
        streams = bench_loop.run_until_complete(
            _subscribed(memory_broker, stack, subscribers)
        )
        try:
            memory_broker.publish("room:1", envelope)
            benchmark(memory_broker.publish, "room:1", envelope)
            bench_loop.run_until_complete(asyncio.sleep(0))
            received = bench_loop.run_until_complete(_first(streams))
            assert all(message is not None for message in received)
        finally:
            bench_loop.run_until_complete(_closed(stack, streams))


class TestBenchDecodeMessage:
    """Decode one Redis Pub/Sub payload, the work of every subscribing process."""

    @pytest.mark.parametrize(
        "envelope_factory", [small_envelope, medium_envelope], ids=["small", "medium"]
    )
    @pytest.mark.benchmark(group="realtime.fanout")
    def test_decode(self, envelope_factory, benchmark) -> None:
        sent = Message("room:1", 3, envelope_factory(), 1.5, "0123456789abcdef")
        payload = json.dumps(sent.as_dict())
        message = benchmark(decode_message, payload)
        assert message == sent
