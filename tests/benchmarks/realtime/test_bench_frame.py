import json
from collections.abc import Callable

import pytest

from next.contrib.channels.consumers import RealtimeConsumer
from next.partial import Envelope, JsonPartialProtocolBackend, StreamMeta
from tests.benchmarks.factories import (
    medium_envelope,
    run_without_loop,
    small_envelope,
    topic_message,
)


_MESSAGE = topic_message()
_META = StreamMeta(
    server_time=1_700_000_000_000,
    topic=_MESSAGE.topic,
    seq=_MESSAGE.seq,
    epoch=_MESSAGE.epoch,
)


class TestBenchSseFrame:
    """Serialise one topic envelope into an SSE event frame."""

    @pytest.mark.parametrize(
        "envelope_factory", [small_envelope, medium_envelope], ids=["small", "medium"]
    )
    @pytest.mark.benchmark(group="realtime.frame")
    def test_envelope(
        self, envelope_factory: Callable[[], Envelope], benchmark
    ) -> None:
        backend = JsonPartialProtocolBackend()
        envelope = envelope_factory()
        benchmark(backend.sse_stream_event, envelope, _META)


class TestBenchWebSocketFrame:
    """Deliver one message as a WebSocket patches frame without an event loop."""

    @pytest.mark.benchmark(group="realtime.frame")
    def test_deliver_message(self, benchmark) -> None:
        sent: list[dict[str, object]] = []

        async def collect(event: dict[str, object]) -> None:
            sent[:] = [event]

        consumer = RealtimeConsumer()
        consumer.base_send = collect

        def run() -> None:
            run_without_loop(consumer._deliver_message(_MESSAGE, None))

        benchmark(run)
        assert json.loads(sent[0]["text"])["type"] == "patches"
