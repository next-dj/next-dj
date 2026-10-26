import pytest

from next.partial import Patches
from next.realtime import MemoryBroker, RedisBroker, publish
from tests.benchmarks.factories import small_envelope


class TestBenchBrokerPublish:
    """Number and store one envelope on a single broker."""

    @pytest.mark.benchmark(group="realtime.publish")
    def test_publish(self, any_broker: MemoryBroker | RedisBroker, benchmark) -> None:
        """Runs the sequence script on the fake server for Redis."""
        envelope = small_envelope()
        benchmark(any_broker.publish, "room:1", envelope)

    @pytest.mark.benchmark(group="realtime.publish")
    @pytest.mark.parametrize("replay", [0], indirect=True, ids=["log-off"])
    @pytest.mark.usefixtures("replay")
    def test_publish_without_replay(
        self, any_broker: MemoryBroker | RedisBroker, benchmark
    ) -> None:
        """Numbers the envelope with the log off, the cost before replay."""
        envelope = small_envelope()
        benchmark(any_broker.publish, "room:1", envelope)


class TestBenchPublicPublish:
    """Publish through the configured broker, paying validation, snapshot and signal."""

    @pytest.mark.benchmark(group="realtime.publish")
    def test_full_publish(self, configured_broker, benchmark) -> None:
        def run() -> None:
            publish("room:1", Patches(None).event("saved", {"id": 7}), on_commit=False)

        benchmark(run)
