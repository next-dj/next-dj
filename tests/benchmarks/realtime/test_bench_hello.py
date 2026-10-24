import json
from collections.abc import Mapping

import pytest

from next.partial import JsonPartialProtocolBackend
from next.partial.stream import StreamHello
from next.realtime import Position
from tests.benchmarks.factories import published_topics


def _hello(positions: Mapping[str, Position]) -> StreamHello:
    return StreamHello(
        server_time=1_700_000_000_000,
        topics={topic: (p.epoch, p.seq) for topic, p in positions.items()},
        transports=("ws", "sse"),
    )


class TestBenchPositions:
    """Read the latest position of every subscribed topic in one batch."""

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.hello")
    def test_positions(self, any_broker, count: int, benchmark) -> None:
        topics = published_topics(any_broker, count)
        positions = benchmark(any_broker.positions, topics)
        assert set(positions) == set(topics)
        assert all(
            position.seq == 1 and position.epoch for position in positions.values()
        )


class TestBenchHelloFrame:
    """Build the handshake frame from positions and serialise it for SSE."""

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.hello")
    def test_build_and_serialise(self, memory_broker, count: int, benchmark) -> None:
        topics = published_topics(memory_broker, count)
        protocol = JsonPartialProtocolBackend()
        positions = memory_broker.positions(topics)

        def run() -> str:
            hello = _hello(positions)
            return protocol.sse_hello(hello.as_dict())

        frame = benchmark(run)
        body = json.loads(frame.split("data: ", 1)[1])
        assert set(body["topics"]) == set(topics)
        assert {t: v["epoch"] for t, v in body["topics"].items()} == {
            t: p.epoch for t, p in positions.items()
        }
