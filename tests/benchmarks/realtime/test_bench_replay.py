import pytest

from next.realtime.backends import MemoryBroker
from next.realtime.messages import Position
from next.realtime.redis.broker import RedisBroker
from next.realtime.replay import catch_up, parse_event_id
from tests.benchmarks.factories import published_topics, small_envelope


_STALE_EPOCH = "0" * 16


def _published(broker: MemoryBroker | RedisBroker, count: int) -> Position:
    envelope = small_envelope()
    for _ in range(count):
        broker.publish("room:1", envelope)
    return broker.positions(["room:1"])["room:1"]


class TestBenchBrokerReplay:
    """Read the log of one topic after a reconnecting client's cursor."""

    @pytest.mark.parametrize("behind", [1, 64])
    @pytest.mark.benchmark(group="realtime.replay")
    def test_after_cursor(
        self, any_broker: MemoryBroker | RedisBroker, behind: int, benchmark
    ) -> None:
        """The cursor trails the topic by ``behind`` messages of the same epoch."""
        current = _published(any_broker, behind + 1)
        cursor = Position(current.epoch, current.seq - behind)
        replay = benchmark(any_broker.replay, "room:1", cursor)
        assert replay.complete
        assert [message.seq for message in replay.messages] == list(
            range(cursor.seq + 1, current.seq + 1)
        )

    @pytest.mark.benchmark(group="realtime.replay")
    def test_stale_epoch(
        self, any_broker: MemoryBroker | RedisBroker, benchmark
    ) -> None:
        """A cursor from a previous epoch reads the log and reports the gap."""
        current = _published(any_broker, 65)
        assert current.epoch != _STALE_EPOCH
        replay = benchmark(any_broker.replay, "room:1", Position(_STALE_EPOCH, 1))
        assert not replay.complete
        assert replay.messages == ()


class TestBenchCatchUp:
    """Collect what every cursor of a hello missed up to the position snapshot."""

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.replay")
    def test_one_behind_per_topic(
        self, any_broker: MemoryBroker | RedisBroker, count: int, benchmark
    ) -> None:
        """Each of ``count`` topics trails its snapshot by one message."""
        topics = published_topics(any_broker, count)
        published_topics(any_broker, count)
        positions = any_broker.positions(topics)
        cursors = {
            topic: Position(position.epoch, position.seq - 1)
            for topic, position in positions.items()
        }
        missed = benchmark(catch_up, any_broker, cursors, positions)
        assert [message.topic for message in missed] == topics
        assert all(message.seq == 2 for message in missed)


class TestBenchParseEventId:
    """Read an SSE ``Last-Event-ID`` header as a cursor."""

    @pytest.mark.benchmark(group="realtime.replay")
    def test_valid(self, benchmark) -> None:
        position = benchmark(parse_event_id, "0123456789abcdef:42")
        assert position == Position("0123456789abcdef", 42)

    @pytest.mark.benchmark(group="realtime.replay")
    def test_malformed(self, benchmark) -> None:
        """A forged id fails the pattern and resumes from the start."""
        assert benchmark(parse_event_id, "e1:42") is None
