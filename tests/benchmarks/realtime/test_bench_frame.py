import json
from collections.abc import Callable

import pytest
from django.contrib.auth.models import AnonymousUser

from next.partial import Envelope, JsonPartialProtocolBackend, StreamMeta
from next.realtime import frames
from next.realtime.socket import Handshake, TopicSocket
from next.testing.realtime import MemoryPort
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
    """Encode one message as a WebSocket patches frame without an event loop."""

    @pytest.mark.benchmark(group="realtime.frame")
    def test_patches_frame(self, benchmark) -> None:
        backend = JsonPartialProtocolBackend()
        text = benchmark(
            frames.patches, _MESSAGE, backend, server_time=_META.server_time
        )
        assert json.loads(text)["type"] == "patches"

    @pytest.mark.benchmark(group="realtime.frame")
    def test_socket_frame(self, benchmark) -> None:
        handshake = Handshake("key", AnonymousUser(), None)
        socket = TopicSocket(MemoryPort(), handshake, heartbeat=25.0)

        def run() -> str:
            return run_without_loop(socket._frame_for(_MESSAGE))

        text = benchmark(run)
        assert json.loads(text)["type"] == "patches"
