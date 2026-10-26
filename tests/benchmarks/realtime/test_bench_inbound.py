import pytest

from next.realtime import frames
from tests.support.cases.realtime import INBOUND_FRAME_CASES, InboundFrameCase


class TestBenchInboundFrame:
    """Decode one client text frame, the work every socket does per heartbeat pong."""

    @pytest.mark.parametrize("case", INBOUND_FRAME_CASES, ids=lambda case: case.id)
    @pytest.mark.benchmark(group="realtime.inbound")
    def test_parse(self, case: InboundFrameCase, benchmark) -> None:
        frame = benchmark(frames.parse, case.text)
        assert isinstance(frame, case.kind)
