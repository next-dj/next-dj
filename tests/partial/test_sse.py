import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncRequestFactory, RequestFactory, override_settings

from next.partial import Envelope, JsonPartialProtocolBackend, Patches, PatchEventStream
from next.partial.manager import partial_backend_manager
from next.partial.signals import sse_stream_closed, sse_stream_opened
from next.partial.sse import _retry_ms, sse_heartbeat_seconds
from next.partial.stream import StreamEnd, StreamMeta
from next.testing import capture_signals
from tests.support.sse import StalledSend, serve_stream


@contextmanager
def _partial_backend_config(backends: list[dict]) -> Iterator[None]:
    """Activate a PARTIAL_BACKENDS config with a manager reset around it."""
    with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": backends}):
        partial_backend_manager.reset()
        try:
            yield
        finally:
            partial_backend_manager.reset()


@pytest.fixture()
def request_obj() -> object:
    """Return a bare WSGI GET request to bind a sync stream to."""
    return RequestFactory().get("/polls/7/stream/")


@pytest.fixture()
def async_request_obj() -> object:
    """Return a bare ASGI GET request to bind an async stream to.

    Django buffers an async iterator under WSGI, so the source-kind guard needs ASGI.
    """
    return AsyncRequestFactory().get("/polls/7/stream/")


def _consume(response: PatchEventStream) -> list[bytes]:
    """Drain the sync streaming content into a list of byte frames."""
    return list(response.streaming_content)


async def _aconsume(response: PatchEventStream) -> list[bytes]:
    """Drain the async streaming content into a list of byte frames."""
    return [frame async for frame in response.streaming_content]


def _patches(*, echo_of: str | None = None) -> Patches:
    """Build a request-free builder with one refresh op."""
    builder = Patches.versioned("v1", echo_of=echo_of)
    builder.refresh(zone="poll-results")
    return builder


class TestSyncStream:
    """A sync source streams a retry hint then one event per envelope."""

    def test_leads_with_retry_then_events(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, [_patches(), _patches()])
        frames = _consume(response)
        assert frames[0].startswith(b"retry: ")
        assert frames[1].startswith(b"event: next-patches")
        assert frames[2].startswith(b"event: next-patches")
        assert len(frames) == 3

    def test_no_heartbeat_on_sync_source(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()))
        frames = _consume(response)
        assert frames == [response._retry_frame()]
        assert not any(frame.startswith(b":") for frame in frames)

    def test_sync_source_can_request_a_heartbeat(self, request_obj: object) -> None:
        source: list[Patches | None] = [None, _patches()]
        frames = _consume(PatchEventStream(request_obj, source))
        assert frames[1] == b": heartbeat\n\n"
        assert frames[2].startswith(b"event: next-patches")

    def test_echo_id_rides_the_event(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, [_patches(echo_of="r1")])
        frames = _consume(response)
        assert b'"request_id":"r1"' in frames[1]

    def test_event_frame_accepts_an_envelope_value(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()))
        frame = response._event_frame(Envelope(version="v1"))
        assert frame.startswith(b"event: next-patches")
        assert b'"server_time":' in frame

    def test_event_frame_rejects_an_unsupported_source_item(
        self, request_obj: object
    ) -> None:
        response = PatchEventStream(request_obj, iter(()))
        with pytest.raises(TypeError, match="must yield a patch"):
            response._event_frame(object())


class TestPolitenessHeaders:
    """The constructor sets the buffering-proof headers and the content type."""

    def test_headers_and_content_type(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()))
        assert response["Content-Type"].startswith("text/event-stream")
        assert response["Cache-Control"] == "no-cache, no-transform"
        assert response["X-Accel-Buffering"] == "no"
        _consume(response)


_NO_SSE_BACKEND = [
    {"BACKEND": "next.partial.JsonPartialProtocolBackend", "OPTIONS": {}}
]


class TestRetryOption:
    """The retry hint reads from the active backend's SSE options."""

    def test_default_retry_in_frame(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()))
        frames = _consume(response)
        assert frames[0] == b"retry: 3000\n\n"

    def test_retry_falls_back_without_sse_option(self) -> None:
        with _partial_backend_config(_NO_SSE_BACKEND):
            assert _retry_ms() == 3000

    @pytest.mark.parametrize("value", [0, 1500], ids=["zero", "whole"])
    def test_retry_keeps_a_whole_millisecond_value(
        self, request_obj: object, value: int
    ) -> None:
        backend = [
            {
                "BACKEND": "next.partial.JsonPartialProtocolBackend",
                "OPTIONS": {"SSE": {"RETRY_MS": value}},
            }
        ]
        with _partial_backend_config(backend):
            frames = _consume(PatchEventStream(request_obj, iter(())))
        assert frames[0] == f"retry: {value}\n\n".encode()

    @pytest.mark.parametrize(
        "value",
        ["fast", True, 0.5, 1500.0, -1],
        ids=["non_numeric", "bool", "fraction", "float", "negative"],
    )
    def test_retry_falls_back_on_a_value_that_is_no_interval(
        self, value: object
    ) -> None:
        backend = [
            {
                "BACKEND": "next.partial.JsonPartialProtocolBackend",
                "OPTIONS": {"SSE": {"RETRY_MS": value}},
            }
        ]
        with _partial_backend_config(backend):
            assert _retry_ms() == 3000


def _custom_heartbeat_backend(seconds: object) -> list[dict]:
    """Return a backend config carrying a custom SSE heartbeat value."""
    return [
        {
            "BACKEND": "next.partial.JsonPartialProtocolBackend",
            "OPTIONS": {"SSE": {"HEARTBEAT_SECONDS": seconds}},
        }
    ]


class TestHeartbeatOption:
    """The heartbeat interval reads from the active backend's SSE options."""

    def test_config_value_applied_when_argument_omitted(
        self, request_obj: object
    ) -> None:
        response = PatchEventStream(request_obj, iter(()))
        assert response._heartbeat_seconds == 25.0

    def test_explicit_argument_overrides_config(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()), heartbeat_seconds=2)
        assert response._heartbeat_seconds == 2

    def test_zero_argument_overrides_config(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()), heartbeat_seconds=0)
        assert response._heartbeat_seconds == 0

    def test_custom_setting_is_picked_up(self, request_obj: object) -> None:
        with _partial_backend_config(_custom_heartbeat_backend(10)):
            response = PatchEventStream(request_obj, iter(()))
            assert response._heartbeat_seconds == 10.0

    def test_falls_back_without_sse_option(self) -> None:
        with _partial_backend_config(_NO_SSE_BACKEND):
            assert sse_heartbeat_seconds() == 25.0

    @pytest.mark.parametrize(
        "seconds", ["nope", True, 0, -1.0], ids=["non_numeric", "bool", "zero", "neg"]
    )
    def test_invalid_heartbeat_value_falls_back(self, seconds: object) -> None:
        with _partial_backend_config(_custom_heartbeat_backend(seconds)):
            assert sse_heartbeat_seconds() == 25.0


class _Recorder:
    """Append every signal payload it receives to a list."""

    def __init__(self) -> None:
        """Start with an empty event list."""
        self.events: list[dict] = []

    def __call__(self, **kwargs) -> None:
        """Record one signal payload."""
        self.events.append(kwargs)


class TestSignals:
    """The stream announces open on construction and close at exhaustion."""

    def test_open_and_close_fire(self, request_obj: object) -> None:
        opened = _Recorder()
        closed = _Recorder()
        sse_stream_opened.connect(opened, weak=False)
        sse_stream_closed.connect(closed, weak=False)
        try:
            response = PatchEventStream(request_obj, [_patches()])
            assert len(opened.events) == 0
            _consume(response)
            assert len(opened.events) == 1
            assert len(closed.events) == 1
            assert closed.events[0]["envelopes_sent"] == 1
            assert closed.events[0]["duration_ms"] >= 0
        finally:
            sse_stream_opened.disconnect(opened)
            sse_stream_closed.disconnect(closed)

    def test_injected_clock_drives_close_duration(self, request_obj: object) -> None:
        ticks = iter([1.0, 2.5])
        closed = _Recorder()
        sse_stream_closed.connect(closed, weak=False)
        try:
            response = PatchEventStream(
                request_obj, iter(()), clock=lambda: next(ticks)
            )
            _consume(response)
            assert closed.events[0]["duration_ms"] == pytest.approx(1500.0)
        finally:
            sse_stream_closed.disconnect(closed)

    def test_close_silent_without_receivers(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, [_patches()])
        assert _consume(response)[1].startswith(b"event: next-patches")


class _ReleasingSource:
    """Async source whose pull blocks until the test releases each item."""

    def __init__(self, items: list[Patches]) -> None:
        """Hold the items and a gate the test opens per pull."""
        self._items = items
        self.gate = asyncio.Event()

    def __aiter__(self) -> AsyncIterator[Patches]:
        """Return the async iterator over the gated items."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Patches]:
        """Yield each item only after the gate is opened, then re-close it."""
        for item in self._items:
            await self.gate.wait()
            self.gate.clear()
            yield item


async def _drain_until_stop(stream: AsyncIterator[bytes]) -> list[bytes]:
    """Pull frames until the async stream raises StopAsyncIteration."""
    frames: list[bytes] = []
    while True:
        try:
            frames.append(await stream.__anext__())
        except StopAsyncIteration:
            return frames


async def _heartbeat_then_event(request_obj: object) -> list[bytes]:
    """Drain a gated async source under a zero heartbeat, releasing one item."""
    source = _ReleasingSource([_patches()])
    response = PatchEventStream(request_obj, source, heartbeat_seconds=0)
    stream = response.streaming_content
    frames = [await stream.__anext__(), await stream.__anext__()]
    source.gate.set()
    frames.extend(await _drain_until_stop(stream))
    return frames


class TestAsyncHeartbeat:
    """An async source under a zero heartbeat interleaves comment frames."""

    def test_pending_pull_emits_heartbeat_then_event(
        self, async_request_obj: object
    ) -> None:
        frames = asyncio.run(_heartbeat_then_event(async_request_obj))
        assert frames[0].startswith(b"retry: ")
        assert frames[1] == b": heartbeat\n\n"
        assert frames[2].startswith(b"event: next-patches")

    def test_async_source_can_request_a_heartbeat(
        self, async_request_obj: object
    ) -> None:
        async def source():
            yield None

        async def consume() -> list[bytes]:
            return await _aconsume(PatchEventStream(async_request_obj, source()))

        assert asyncio.run(consume()) == [b"retry: 3000\n\n", b": heartbeat\n\n"]

    def test_async_close_counts_only_events(self, async_request_obj: object) -> None:
        closed = _Recorder()
        sse_stream_closed.connect(closed, weak=False)
        try:
            asyncio.run(_heartbeat_then_event(async_request_obj))
            assert closed.events[0]["envelopes_sent"] == 1
        finally:
            sse_stream_closed.disconnect(closed)


class TestSourceKindDetection:
    """The stream picks the sync or async pipeline by the source kind."""

    def test_sync_iterable_is_not_async(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter([_patches()]))
        assert response.is_async is False
        _consume(response)

    def test_async_iterable_is_async(self, async_request_obj: object) -> None:
        async def source() -> AsyncIterator[Patches]:
            yield _patches()

        response = PatchEventStream(async_request_obj, source())
        assert response.is_async is True
        asyncio.run(_aconsume(response))


class TestSourceServerKindGuard:
    """A source kind the server kind would buffer is refused at construction."""

    def test_async_source_under_wsgi_raises(self, request_obj: object) -> None:
        async def source() -> AsyncIterator[Patches]:
            yield _patches()

        with pytest.raises(ImproperlyConfigured, match="async source under a WSGI"):
            PatchEventStream(request_obj, source())

    def test_sync_source_under_asgi_raises(self, async_request_obj: object) -> None:
        with pytest.raises(ImproperlyConfigured, match="sync source under an ASGI"):
            PatchEventStream(async_request_obj, [_patches()])


class _FinalizedSource:
    """Async source that records its generator finalization in a flag."""

    def __init__(self) -> None:
        """Start unreleased and unfinalized."""
        self.gate = asyncio.Event()
        self.finalized = False

    def __aiter__(self) -> AsyncIterator[Patches]:
        """Return the async iterator over the gated single item."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Patches]:
        """Block on the gate, marking finalized in the closing finally."""
        try:
            await self.gate.wait()
            yield _patches()
        finally:
            self.finalized = True


async def _disconnect_midpull(async_request_obj: object) -> _FinalizedSource:
    """Drive a stream into a pending pull, then disconnect by closing it."""
    source = _FinalizedSource()
    response = PatchEventStream(async_request_obj, source, heartbeat_seconds=0)
    stream = response.streaming_content
    await stream.__anext__()
    await stream.__anext__()
    await stream.aclose()
    return source


class _SyncFinalizedSource:
    """Sync source that records its generator finalization in a flag."""

    def __init__(self) -> None:
        """Start unfinalized with a fresh body generator."""
        self.finalized = False
        self._gen = self._iterate()

    def __iter__(self) -> Iterator[Patches]:
        """Return the single body generator."""
        return self._gen

    def _iterate(self) -> Iterator[Patches]:
        """Yield two items, marking finalized in the closing finally."""
        try:
            yield _patches()
            yield _patches()
        finally:
            self.finalized = True

    def close(self) -> None:
        """Close the body generator so the stream can release it."""
        self._gen.close()


class TestSyncSourceCleanup:
    """The sync stream closes its source generator on end or disconnect."""

    def test_exhaustion_closes_the_source(self, request_obj: object) -> None:
        source = _SyncFinalizedSource()
        response = PatchEventStream(request_obj, source)
        _consume(response)
        assert source.finalized is True

    def test_disconnect_closes_the_source(self, request_obj: object) -> None:
        source = _SyncFinalizedSource()
        response = PatchEventStream(request_obj, source)
        stream = response.streaming_content
        next(stream)
        next(stream)
        # a mid-stream disconnect closes the byte generator and then the source
        response._iterator.close()
        assert source.finalized is True


class TestAsyncDisconnectCleanup:
    """A disconnect mid-pull cancels the task and finalizes the source."""

    def test_pending_pull_is_cleaned_up_on_disconnect(
        self, async_request_obj: object
    ) -> None:
        closed = _Recorder()
        sse_stream_closed.connect(closed, weak=False)
        try:
            source = asyncio.run(_disconnect_midpull(async_request_obj))
        finally:
            sse_stream_closed.disconnect(closed)
        assert source.finalized is True
        assert closed.events[0]["envelopes_sent"] == 0


class _PlainAsyncSource:
    """Async source whose iterator is a plain object, not a generator."""

    def __init__(self, count: int) -> None:
        self.left = count

    def __aiter__(self) -> "_PlainAsyncSource":
        return self

    async def __anext__(self) -> Patches:
        if self.left == 0:
            raise StopAsyncIteration
        self.left -= 1
        return _patches()


class TestAsyncSourceWithoutAclose:
    """An async iterator that is no generator has nothing to close."""

    def test_the_stream_ends_and_announces_the_close(
        self, async_request_obj: object
    ) -> None:
        closed = _Recorder()
        sse_stream_closed.connect(closed, weak=False)
        try:
            response = PatchEventStream(async_request_obj, _PlainAsyncSource(2))
            frames = asyncio.run(_aconsume(response))
        finally:
            sse_stream_closed.disconnect(closed)
        assert len(frames) == 3
        assert closed.events[0]["envelopes_sent"] == 2


class _FailingCloseSource:
    """Sync source whose close fails, as a broken broker connection would."""

    def __iter__(self) -> Iterator[Patches]:
        return iter([_patches()])

    def close(self) -> None:
        msg = "broker gone"
        raise ConnectionError(msg)


class _FailingAcloseSource:
    """Async source whose aclose fails, as a broken broker connection would."""

    def __init__(self) -> None:
        self.left = 1

    def __aiter__(self) -> "_FailingAcloseSource":
        return self

    async def __anext__(self) -> Patches:
        if self.left == 0:
            raise StopAsyncIteration
        self.left -= 1
        return _patches()

    async def aclose(self) -> None:
        msg = "broker gone"
        raise ConnectionError(msg)


class TestCleanupFailure:
    """A failing source cleanup still announces the close and then propagates."""

    def test_sync_close_failure_still_fires_the_close_signal(
        self, request_obj: object
    ) -> None:
        response = PatchEventStream(request_obj, _FailingCloseSource())
        with (
            capture_signals(sse_stream_closed) as recorder,
            pytest.raises(ConnectionError, match="broker gone"),
        ):
            _consume(response)
        assert recorder.first_for(sse_stream_closed).kwargs["envelopes_sent"] == 1

    def test_async_aclose_failure_still_fires_the_close_signal(
        self, async_request_obj: object
    ) -> None:
        response = PatchEventStream(async_request_obj, _FailingAcloseSource())
        with (
            capture_signals(sse_stream_closed) as recorder,
            pytest.raises(ConnectionError, match="broker gone"),
        ):
            asyncio.run(_aconsume(response))
        assert recorder.first_for(sse_stream_closed).kwargs["envelopes_sent"] == 1


class TestOpenedSignalListeners:
    """The open signal is only built when someone listens."""

    def test_open_signal_is_skipped_without_receivers(
        self, request_obj: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[object] = []
        monkeypatch.setattr(
            sse_stream_opened, "send_robust", lambda **kw: sent.append(kw)
        )
        monkeypatch.setattr(sse_stream_opened, "has_listeners", lambda _s=None: False)
        _consume(PatchEventStream(request_obj, [_patches()]))
        assert sent == []


def _in_event_loop() -> bool:
    """Whether the calling thread runs an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


async def _agen() -> AsyncIterator[Patches]:
    yield _patches()


_FAILED_RECEIVER = (
    "Error calling _failing_receiver in Signal.send_robust() (observer broke)"
)


def _failing_receiver(**kwargs: object) -> None:
    msg = "observer broke"
    raise RuntimeError(msg)


class TestSignalDelivery:
    """Stream signals reach sync receivers off the loop and never break the stream."""

    def test_async_stream_runs_sync_receivers_off_the_loop(
        self, async_request_obj: object
    ) -> None:
        seen: list[tuple[str, bool]] = []

        def opened(**kwargs: object) -> None:
            seen.append(("opened", _in_event_loop()))

        def closed(**kwargs: object) -> None:
            seen.append(("closed", _in_event_loop()))

        sse_stream_opened.connect(opened, weak=False)
        sse_stream_closed.connect(closed, weak=False)
        try:
            asyncio.run(_aconsume(PatchEventStream(async_request_obj, _agen())))
        finally:
            sse_stream_opened.disconnect(opened)
            sse_stream_closed.disconnect(closed)
        assert seen == [("opened", False), ("closed", False)]

    def test_async_stream_awaits_async_receivers(
        self, async_request_obj: object
    ) -> None:
        heard: list[object] = []

        async def opened(**kwargs: object) -> None:
            heard.append(("opened", kwargs["request"]))

        async def closed(**kwargs: object) -> None:
            heard.append(("closed", kwargs["envelopes_sent"]))

        sse_stream_opened.connect(opened, weak=False)
        sse_stream_closed.connect(closed, weak=False)
        try:
            asyncio.run(_aconsume(PatchEventStream(async_request_obj, _agen())))
        finally:
            sse_stream_opened.disconnect(opened)
            sse_stream_closed.disconnect(closed)
        assert heard == [("opened", async_request_obj), ("closed", 1)]

    def test_failing_receiver_leaves_the_sync_stream_whole(
        self, request_obj: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        sse_stream_opened.connect(_failing_receiver, weak=False)
        sse_stream_closed.connect(_failing_receiver, weak=False)
        try:
            frames = _consume(PatchEventStream(request_obj, [_patches()]))
        finally:
            sse_stream_opened.disconnect(_failing_receiver)
            sse_stream_closed.disconnect(_failing_receiver)
        assert frames[0] == b"retry: 3000\n\n"
        assert frames[1].startswith(b"event: next-patches")
        assert [r.getMessage() for r in caplog.records].count(_FAILED_RECEIVER) == 2

    def test_failing_receiver_leaves_the_async_stream_whole(
        self, async_request_obj: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        sse_stream_opened.connect(_failing_receiver, weak=False)
        sse_stream_closed.connect(_failing_receiver, weak=False)
        try:
            response = PatchEventStream(async_request_obj, _agen())
            frames = asyncio.run(_aconsume(response))
        finally:
            sse_stream_opened.disconnect(_failing_receiver)
            sse_stream_closed.disconnect(_failing_receiver)
        assert frames[0] == b"retry: 3000\n\n"
        assert frames[1].startswith(b"event: next-patches")
        assert [r.getMessage() for r in caplog.records].count(_FAILED_RECEIVER) == 2


class TestTopicFrameMetadata:
    """A topic message carries its epoch into the stream metadata."""

    def test_topic_message_frame_includes_epoch(self, request_obj: object) -> None:
        message = SimpleNamespace(
            envelope=_patches().envelope(), topic="room:1", seq=3, epoch="e9"
        )
        frames = _consume(PatchEventStream(request_obj, [message]))
        assert b'"topic":"room:1","epoch":"e9","seq":3' in frames[1]


class _NumberedStream(PatchEventStream):
    def event_id(self, message) -> str | None:
        return f"{message.epoch}:{message.seq}"


class _TextBackend(JsonPartialProtocolBackend):
    def stream_data(self, envelope: Envelope, meta: StreamMeta) -> str:
        return str(len(envelope.ops))


def _topic_message(epoch: str = "e9") -> SimpleNamespace:
    return SimpleNamespace(
        envelope=_patches().envelope(), topic="room:1", seq=3, epoch=epoch
    )


class TestTopicFrameEventId:
    """The writer prefixes the id its `event_id` hook names to a topic frame."""

    def test_the_default_hook_names_no_id(self, request_obj: object) -> None:
        frames = _consume(PatchEventStream(request_obj, [_topic_message()]))
        assert frames[1].startswith(b"event: next-patches\n")
        assert b"id:" not in frames[1]

    def test_a_named_id_leads_the_frame(self, request_obj: object) -> None:
        frames = _consume(_NumberedStream(request_obj, [_topic_message(), _patches()]))
        assert frames[1].startswith(b"id: e9:3\nevent: next-patches\n")
        assert b"id:" not in frames[2]

    @pytest.mark.parametrize("epoch", ["e9\nevent: x", "e9\r", "e9\0"])
    def test_an_id_that_would_break_the_framing_is_dropped(
        self, request_obj: object, epoch: str
    ) -> None:
        frames = _consume(_NumberedStream(request_obj, [_topic_message(epoch)]))
        assert frames[1].startswith(b"event: next-patches\n")

    def test_a_custom_protocol_backend_keeps_the_id(self, request_obj: object) -> None:
        backend = {"BACKEND": "tests.partial.test_sse._TextBackend", "OPTIONS": {}}
        with _partial_backend_config([backend]):
            frames = _consume(_NumberedStream(request_obj, [_topic_message()]))
        assert frames[1] == b"id: e9:3\nevent: next-patches\ndata: 1\n\n"


class TestEndFrame:
    """The terminal frame is a control frame and never counts as an envelope."""

    def test_end_frame_is_written_and_not_counted(self, request_obj: object) -> None:
        source = [_patches(), StreamEnd("auth")]
        with capture_signals(sse_stream_closed) as recorder:
            frames = _consume(PatchEventStream(request_obj, source))
        assert frames[-1] == b'event: next-end\ndata: {"code":"auth"}\n\n'
        assert recorder.first_for(sse_stream_closed).kwargs["envelopes_sent"] == 1


class _EndlessSource:
    """Async source that yields one item, then waits for good until it is closed."""

    def __init__(self) -> None:
        """Start unfinalized."""
        self.finalized = False

    def __aiter__(self) -> AsyncIterator[Patches]:
        """Return the async iterator over the one item and the endless wait."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Patches]:
        """Yield one item, block forever, and mark finalized in the closing finally."""
        try:
            yield _patches()
            await asyncio.Event().wait()
        finally:
            self.finalized = True


class TestStalledWriter:
    """A client that stops reading is dropped once a write blocks past a heartbeat."""

    def test_a_blocked_send_drops_the_stream_and_closes_the_source(
        self, async_request_obj: object
    ) -> None:
        source = _EndlessSource()

        async def scenario() -> tuple[asyncio.Task[None], StalledSend]:
            response = PatchEventStream(
                async_request_obj, source, heartbeat_seconds=0.01
            )
            send = StalledSend(delivered=1)
            task = await serve_stream(response, send, within=0.5)
            assert task.done()
            assert source.finalized
            return task, send

        with capture_signals(sse_stream_closed) as recorder:
            task, send = asyncio.run(scenario())
        assert task.cancelled()
        assert send.blocked.is_set()
        assert send.bodies == [b"retry: 3000\n\n"]
        assert source.finalized
        assert recorder.first_for(sse_stream_closed).kwargs["envelopes_sent"] == 1

    def test_a_reading_client_outlives_many_heartbeats(
        self, async_request_obj: object
    ) -> None:
        async def source() -> AsyncIterator[Patches]:
            await asyncio.sleep(0.05)
            yield _patches()

        async def scenario() -> asyncio.Task[None]:
            response = PatchEventStream(
                async_request_obj, source(), heartbeat_seconds=0.005
            )
            task = await serve_stream(response)
            assert task.done()
            return task

        task = asyncio.run(scenario())
        assert not task.cancelled()
        assert task.result() is None
