import asyncio
from collections.abc import AsyncIterator

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncRequestFactory, RequestFactory, override_settings

from next.partial import Patches, PatchEventStream
from next.partial.manager import partial_backend_manager
from next.partial.signals import sse_stream_closed, sse_stream_opened
from next.partial.sse import _heartbeat_seconds, _retry_ms


@pytest.fixture()
def request_obj() -> object:
    """Return a bare WSGI GET request to bind a sync stream to."""
    return RequestFactory().get("/polls/7/stream/")


@pytest.fixture()
def async_request_obj() -> object:
    """Return a bare ASGI GET request to bind an async stream to.

    Django buffers an async iterator under WSGI, so an async source needs
    an ASGI request to pass the source-kind guard.
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
    builder = Patches("v1", echo_of=echo_of)
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

    def test_echo_id_rides_the_event(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, [_patches(echo_of="r1")])
        frames = _consume(response)
        assert b'"request_id":"r1"' in frames[1]


class TestPolitenessHeaders:
    """The constructor sets the buffering-proof headers and the content type."""

    def test_headers_and_content_type(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()))
        assert response["Content-Type"].startswith("text/event-stream")
        assert response["Cache-Control"] == "no-cache, no-transform"
        assert response["X-Accel-Buffering"] == "no"
        _consume(response)


_NO_SSE_BACKEND = [
    {
        "BACKEND": "next.partial.PartialProtocolBackend",
        "OPTIONS": {},
    },
]


class TestRetryOption:
    """The retry hint reads from the active backend's SSE options."""

    def test_default_retry_in_frame(self, request_obj: object) -> None:
        response = PatchEventStream(request_obj, iter(()))
        frames = _consume(response)
        assert frames[0] == b"retry: 3000\n\n"

    def test_retry_falls_back_without_sse_option(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": _NO_SSE_BACKEND}):
            partial_backend_manager.reset()
            try:
                assert _retry_ms() == 3000
            finally:
                partial_backend_manager.reset()

    def test_retry_falls_back_on_non_int_value(self) -> None:
        backend = [
            {
                "BACKEND": "next.partial.PartialProtocolBackend",
                "OPTIONS": {"SSE": {"RETRY_MS": "fast"}},
            },
        ]
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": backend}):
            partial_backend_manager.reset()
            try:
                assert _retry_ms() == 3000
            finally:
                partial_backend_manager.reset()


def _custom_heartbeat_backend(seconds: object) -> list[dict]:
    """Return a backend config carrying a custom SSE heartbeat value."""
    return [
        {
            "BACKEND": "next.partial.PartialProtocolBackend",
            "OPTIONS": {"SSE": {"HEARTBEAT_SECONDS": seconds}},
        },
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
        backend = _custom_heartbeat_backend(10)
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": backend}):
            partial_backend_manager.reset()
            try:
                response = PatchEventStream(request_obj, iter(()))
                assert response._heartbeat_seconds == 10.0
            finally:
                partial_backend_manager.reset()

    def test_falls_back_without_sse_option(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": _NO_SSE_BACKEND}):
            partial_backend_manager.reset()
            try:
                assert _heartbeat_seconds() == 25.0
            finally:
                partial_backend_manager.reset()

    def test_non_numeric_value_falls_back(self) -> None:
        backend = _custom_heartbeat_backend("nope")
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": backend}):
            partial_backend_manager.reset()
            try:
                assert _heartbeat_seconds() == 25.0
            finally:
                partial_backend_manager.reset()

    def test_bool_value_falls_back(self) -> None:
        backend = _custom_heartbeat_backend(True)
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": backend}):
            partial_backend_manager.reset()
            try:
                assert _heartbeat_seconds() == 25.0
            finally:
                partial_backend_manager.reset()


class _Recorder:
    """Append every signal payload it receives to a list."""

    def __init__(self) -> None:
        """Start with an empty event list."""
        self.events: list[dict] = []

    def __call__(self, **kwargs: object) -> None:
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
            assert len(opened.events) == 1
            _consume(response)
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
