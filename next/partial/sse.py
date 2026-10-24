"""SSE bridge streaming patch envelopes over the page render escape hatch."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING, cast

from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.http import StreamingHttpResponse

from .envelope import Envelope
from .headers import set_partial_vary
from .manager import partial_backend_manager
from .signals import sse_stream_closed, sse_stream_opened
from .stream import StreamEnd, StreamHello, StreamMeta, StreamSkip


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
    from typing import Protocol

    from django.http import HttpRequest

    from .patches import Patches

    class _TopicMessage(Protocol):
        @property
        def envelope(self) -> Envelope: ...

        @property
        def topic(self) -> str: ...

        @property
        def seq(self) -> int: ...

        @property
        def epoch(self) -> str: ...

    type ControlFrame = StreamHello | StreamSkip | StreamEnd
    type PatchItem = (
        Patches | Envelope | _TopicMessage | StreamHello | StreamSkip | StreamEnd | None
    )
    PatchSource = Iterable[PatchItem] | AsyncIterable[PatchItem]


_EVENT_STREAM = "text/event-stream"
_CACHE_CONTROL = "no-cache, no-transform"
_ACCEL_BUFFERING = "X-Accel-Buffering"
_HEARTBEAT_COMMENT = ": heartbeat\n\n"
_RETRY_OPTION = "RETRY_MS"
_HEARTBEAT_OPTION = "HEARTBEAT_SECONDS"
_SSE_OPTION = "SSE"
_DEFAULT_RETRY_MS = 3000
_DEFAULT_HEARTBEAT_SECONDS = 25.0
_CONTROL_FRAMES = (StreamHello, StreamSkip, StreamEnd)


class PatchStreamWriter(StreamingHttpResponse):
    """Shared SSE writer for manual patches and broker topic messages.

    Django buffers a mismatched sync/async iterator, so the constructor refuses it.
    """

    def __init__(
        self,
        request: "HttpRequest",
        source: "PatchSource",
        *,
        heartbeat_seconds: float | None = None,
        clock: "Callable[[], float] | None" = None,
        server_clock: "Callable[[], float] | None" = None,
    ) -> None:
        """Build the stream over a sync or async source of patch builders.

        The heartbeat interval defaults to the backend's `HEARTBEAT_SECONDS` option.
        """
        self._request = request
        self._clock = clock if clock is not None else time.monotonic
        self._server_clock = server_clock if server_clock is not None else time.time
        self._heartbeat_seconds = (
            heartbeat_seconds if heartbeat_seconds is not None else _heartbeat_seconds()
        )
        self._retry_ms = _retry_ms()
        self._opened_at = self._clock()
        self._guard_source_kind(source)
        content = self._build_content(source)
        super().__init__(content, content_type=_EVENT_STREAM)
        self["Cache-Control"] = _CACHE_CONTROL
        self[_ACCEL_BUFFERING] = "no"
        set_partial_vary(self)

    def _guard_source_kind(self, source: "PatchSource") -> None:
        """Refuse a source kind the request's server kind would buffer.

        Django reads a mismatched iterator fully before the first byte, hanging it.
        """
        asgi = isinstance(self._request, ASGIRequest)
        if isinstance(source, AsyncIterable):
            if not asgi:
                msg = (
                    "PatchEventStream got an async source under a WSGI request. "
                    "Django buffers an async iterator fully under WSGI, which "
                    "hangs the stream. Serve this view under ASGI or pass a "
                    "sync source."
                )
                raise ImproperlyConfigured(msg)
        elif asgi:
            msg = (
                "PatchEventStream got a sync source under an ASGI request. "
                "Django buffers a sync iterator fully under ASGI, which hangs "
                "the stream. Pass an async source or serve this view under WSGI."
            )
            raise ImproperlyConfigured(msg)

    def _build_content(
        self, source: "PatchSource"
    ) -> "Iterator[bytes] | AsyncIterator[bytes]":
        """Return a sync or async byte stream matching the source kind."""
        if isinstance(source, AsyncIterable):
            return self._async_stream(source)
        return self._sync_stream(source)

    def _sync_stream(self, source: "Iterable[PatchItem]") -> "Iterator[bytes]":
        """Yield SSE bytes for a sync source, including source-requested heartbeats.

        A blocked `next()` needs a thread to interrupt, so the source yields keepalives.
        """
        sent = 0
        try:
            self._announce_opened()
            yield self._retry_frame()
            for item in source:
                if item is None:
                    yield _HEARTBEAT_COMMENT.encode("utf-8")
                    continue
                frame = self._event_frame(item)
                if not isinstance(item, _CONTROL_FRAMES):
                    sent += 1
                yield frame
        finally:
            try:
                self._close_source(source)
            finally:
                self._announce_closed(sent)

    @staticmethod
    def _close_source(source: "Iterable[PatchItem]") -> None:
        """Close a generator source so a disconnect leaves nothing suspended.

        A plain iterable has no `close`, and closing a spent generator twice is a no-op.
        """
        close = getattr(source, "close", None)
        if callable(close):
            close()

    async def _async_stream(
        self, source: "AsyncIterable[PatchItem]"
    ) -> "AsyncIterator[bytes]":
        """Yield SSE bytes for an async source, interleaving heartbeats.

        One pull task is held across heartbeats so the generator is never re-entered.
        """
        sent = 0
        iterator = source.__aiter__()
        task: asyncio.Future[PatchItem] | None = None
        try:
            self._announce_opened()
            yield self._retry_frame()
            while True:
                if task is None:
                    task = asyncio.ensure_future(iterator.__anext__())
                done, _ = await asyncio.wait({task}, timeout=self._heartbeat_seconds)
                if not done:
                    yield _HEARTBEAT_COMMENT.encode("utf-8")
                    continue
                try:
                    patches = task.result()
                except StopAsyncIteration:
                    break
                finally:
                    task = None
                if patches is None:
                    yield _HEARTBEAT_COMMENT.encode("utf-8")
                    continue
                frame = self._event_frame(patches)
                if not isinstance(patches, _CONTROL_FRAMES):
                    sent += 1
                yield frame
        finally:
            try:
                await self._cleanup_async(task, iterator)
            finally:
                self._announce_closed(sent)

    async def _cleanup_async(
        self,
        task: "asyncio.Future[PatchItem] | None",
        iterator: "AsyncIterator[PatchItem]",
    ) -> None:
        """Cancel an in-flight pull and close the source generator.

        A disconnect throws mid-pull, so the pending task is cancelled and `aclose` run.
        """
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await aclose()

    def _event_frame(
        self, item: "Patches | Envelope | _TopicMessage | ControlFrame"
    ) -> bytes:
        """Serialize a patch envelope or stream control object as an SSE frame."""
        backend = partial_backend_manager.get()
        if isinstance(item, StreamHello):
            return backend.sse_hello(item.as_dict()).encode("utf-8")
        if isinstance(item, StreamSkip):
            return backend.sse_skip(item.as_dict()).encode("utf-8")
        if isinstance(item, StreamEnd):
            return backend.sse_end(item.as_dict()).encode("utf-8")
        message_envelope = getattr(item, "envelope", None)
        meta = StreamMeta(server_time=int(self._server_clock() * 1000))
        if isinstance(item, Envelope):
            envelope = item
        elif isinstance(message_envelope, Envelope):
            envelope = message_envelope
            message = cast("_TopicMessage", item)
            meta = StreamMeta(
                server_time=int(self._server_clock() * 1000),
                topic=message.topic,
                seq=message.seq,
                epoch=message.epoch,
            )
        elif callable(message_envelope):
            envelope = cast("Callable[[], Envelope]", message_envelope)()
        else:
            msg = "SSE patch sources must yield a patch, envelope, or message."
            raise TypeError(msg)
        return backend.sse_stream_event(envelope, meta).encode("utf-8")

    def _announce_opened(self) -> None:
        """Report that streaming starts when iteration produces its first frame."""
        sender = type(self)
        if sse_stream_opened.has_listeners(sender):
            sse_stream_opened.send(sender=sender, request=self._request)

    def _retry_frame(self) -> bytes:
        """Return the leading SSE `retry` hint frame."""
        return f"retry: {self._retry_ms}\n\n".encode()

    def _announce_closed(self, sent: int) -> None:
        """Fire the close signal with the stream's duration and event count."""
        sender = type(self)
        if not sse_stream_closed.has_listeners(sender):
            return
        duration_ms = (self._clock() - self._opened_at) * 1000
        sse_stream_closed.send(
            sender=sender,
            request=self._request,
            duration_ms=duration_ms,
            envelopes_sent=sent,
        )


def _sse_interval(option: str, default: float) -> float:
    """Return one numeric SSE option of the active backend, or its default.

    A bool is an int to Python but no interval, so `True` falls back to the default.
    """
    sse = partial_backend_manager.get().options.get(_SSE_OPTION)
    value = sse.get(option, default) if isinstance(sse, dict) else default
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


def _retry_ms() -> int:
    """Return the EventSource retry hint from the active backend options."""
    return int(_sse_interval(_RETRY_OPTION, _DEFAULT_RETRY_MS))


def _heartbeat_seconds() -> float:
    """Return the heartbeat interval from the active backend options."""
    return _sse_interval(_HEARTBEAT_OPTION, _DEFAULT_HEARTBEAT_SECONDS)


class PatchEventStream(PatchStreamWriter):
    """SSE response that emits manually supplied patch envelopes."""

    def __init__(
        self,
        request: "HttpRequest",
        source: "Iterable[Patches] | AsyncIterable[Patches]",
        *,
        heartbeat_seconds: float | None = None,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        """Build a compatible manual stream from a sync or async patch source."""
        super().__init__(
            request, source, heartbeat_seconds=heartbeat_seconds, clock=clock
        )


__all__ = ["PatchEventStream", "PatchStreamWriter"]
