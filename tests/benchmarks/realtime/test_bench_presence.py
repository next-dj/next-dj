import pytest

from next.realtime.backends import MemoryBroker
from next.realtime.redis.broker import RedisBroker
from next.realtime.roster import Presence, counted, snapshot_message
from tests.benchmarks.factories import joined_connections, quiet_presence, topic_message


class TestBenchPresence:
    """Record, refresh and read the live connections of one topic."""

    @pytest.mark.parametrize("count", [1, 1000])
    @pytest.mark.benchmark(group="realtime.presence")
    def test_join(
        self, any_broker: MemoryBroker | RedisBroker, count: int, benchmark
    ) -> None:
        """One connection joins a roster of ``count``, left again before each round."""
        broker = quiet_presence(any_broker)
        joined_connections(broker, count)

        def leave() -> tuple[tuple[object, ...], dict[str, object]]:
            broker.leave("room:1", "probe")
            return ("room:1", "probe", {"id": "probe"}, 3600), {}

        benchmark.pedantic(broker.join, setup=leave, rounds=500, warmup_rounds=5)
        assert broker.presence("room:1").count == count + 1

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


class TestBenchCounted:
    """Cut the members from a message for a subscriber that does not identify."""

    @pytest.mark.parametrize("members", [1, 100])
    @pytest.mark.benchmark(group="realtime.presence")
    def test_presence_message(self, members: int, benchmark) -> None:
        """A snapshot event loses its members and keeps its count."""
        snapshot = Presence(members, tuple({"id": f"c{i}"} for i in range(members)))
        message = snapshot_message("room:1", snapshot, 1.5)
        cut = benchmark(counted, message)
        assert cut is not message
        assert cut.envelope.ops[0].extras["detail"] == {"count": members}

    @pytest.mark.benchmark(group="realtime.presence")
    def test_plain_message(self, benchmark) -> None:
        """A message without a presence event passes through untouched."""
        message = topic_message()
        assert benchmark(counted, message) is message
