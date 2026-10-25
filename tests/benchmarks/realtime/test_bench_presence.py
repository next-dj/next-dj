import itertools

import pytest

from next.realtime import MemoryBroker, Presence, RedisBroker
from tests.benchmarks.factories import joined_connections, quiet_presence


class TestBenchPresence:
    """Record, refresh and read the live connections of one topic."""

    @pytest.mark.benchmark(group="realtime.presence")
    def test_join(self, any_broker: MemoryBroker | RedisBroker, benchmark) -> None:
        """A fresh connection joins while the coalescing window is already taken."""
        broker = quiet_presence(any_broker)
        ids = itertools.count()

        def run() -> None:
            connection = f"c{next(ids)}"
            broker.join("room:1", connection, {"id": connection}, 3600)

        benchmark(run)
        assert broker.presence("room:1").count == next(ids)

    @pytest.mark.parametrize("count", [1, 1000])
    @pytest.mark.benchmark(group="realtime.presence")
    def test_touch(
        self, any_broker: MemoryBroker | RedisBroker, count: int, benchmark
    ) -> None:
        """A heartbeat pushes one deadline out on a roster of ``count``."""
        broker = quiet_presence(any_broker)
        joined_connections(broker, count)
        assert benchmark(broker.touch, "room:1", "c0", 3600)

    @pytest.mark.parametrize("count", [1, 1000])
    @pytest.mark.benchmark(group="realtime.presence")
    def test_snapshot(
        self, any_broker: MemoryBroker | RedisBroker, count: int, benchmark
    ) -> None:
        """Count ``count`` connections and read up to the default hundred members."""
        broker = quiet_presence(any_broker)
        joined_connections(broker, count)
        snapshot: Presence = benchmark(broker.presence, "room:1")
        assert snapshot.count == count
        assert len(snapshot.members) == min(count, 100)
