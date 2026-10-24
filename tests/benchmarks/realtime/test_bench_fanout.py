from contextlib import ExitStack

import pytest

from next.realtime import MemoryBroker
from tests.benchmarks.factories import small_envelope


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
