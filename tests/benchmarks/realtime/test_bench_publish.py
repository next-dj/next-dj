import pytest

from next.partial import Patches
from next.realtime import MemoryBroker, RedisBroker, publish
from tests.benchmarks.factories import small_envelope


class TestBenchBrokerPublish:
    """Number and store one envelope on a single broker."""

    @pytest.mark.benchmark(group="realtime.publish")
    def test_memory_broker(self, memory_broker: MemoryBroker, benchmark) -> None:
        envelope = small_envelope()
        benchmark(memory_broker.publish, "room:1", envelope)

    @pytest.mark.benchmark(group="realtime.publish")
    def test_redis_broker(self, redis_broker: RedisBroker, benchmark) -> None:
        """Runs the sequence script on the fake server."""
        envelope = small_envelope()
        benchmark(redis_broker.publish, "room:1", envelope)


class TestBenchPublicPublish:
    """Publish through the configured broker, paying validation, snapshot and signal."""

    @pytest.mark.benchmark(group="realtime.publish")
    def test_full_publish(self, configured_broker, benchmark) -> None:
        def run() -> None:
            publish("room:1", Patches(None).event("saved", {"id": 7}), on_commit=False)

        benchmark(run)
