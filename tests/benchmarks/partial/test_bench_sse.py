import pytest
from django.test import RequestFactory

from next.partial.sse import PatchStreamWriter
from tests.benchmarks.factories import topic_message


_HEARTBEATS = 50
_MESSAGES = 20


def _drain(source: list[object]) -> bytes:
    response = PatchStreamWriter(RequestFactory().get("/updates/"), source)
    return b"".join(response.streaming_content)


class TestBenchSseStream:
    """Iterate a finite synchronous stream to its last byte."""

    @pytest.mark.benchmark(group="partial.sse")
    def test_heartbeat_frames(self, benchmark) -> None:
        source: list[object] = [None] * _HEARTBEATS
        benchmark(_drain, source)

    @pytest.mark.benchmark(group="partial.sse")
    def test_topic_message_frames(self, benchmark) -> None:
        """Each frame carries topic, sequence, epoch and server time."""
        source: list[object] = [
            topic_message("room:1", seq) for seq in range(1, _MESSAGES + 1)
        ]
        benchmark(_drain, source)

    @pytest.mark.benchmark(group="partial.sse")
    def test_open_and_close_empty(self, benchmark) -> None:
        source: list[object] = []
        benchmark(_drain, source)
