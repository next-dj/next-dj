"""SSE bridge streaming patch envelopes over the page render escape hatch."""

import asyncio
import contextlib
import math
import time
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING, TypeGuard, cast

from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.http import StreamingHttpResponse

from next.conf.merge import FROZEN_DEFAULTS
from next.utils import positive_number

from .envelope import Envelope
from .headers import set_partial_vary
from .manager import partial_backend_manager
from .signals import sse_stream_closed, sse_stream_opened
from .stream import StreamEnd, StreamHello, StreamMeta, StreamSkip


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
    from typing import Protocol

    from django.http import HttpRequest

    from .backends import PartialProtocolBackend
    from .patches import Patches
    from .stream import StreamPosition

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
RETRY_OPTION = "RETRY_MS"
HEARTBEAT_OPTION = "HEARTBEAT_SECONDS"
SSE_OPTION = "SSE"
_SSE_DEFAULTS = FROZEN_DEFAULTS["PARTIAL_BACKENDS"][0]["OPTIONS"][SSE_OPTION]
_DEFAULT_RETRY_MS: int = _SSE_DEFAULTS[RETRY_OPTION]
_DEFAULT_HEARTBEAT_SECONDS = float(_SSE_DEFAULTS[HEARTBEAT_OPTION])
_CONTROL_FRAMES = (StreamHello, StreamSkip, StreamEnd)
_UNSAFE_ID = frozenset("\r\n\0")


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
            heartbeat_seconds
            if heartbeat_seconds is not None
            else sse_heartbeat_seconds()
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
            await self._aannounce_opened()
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
                await self._aannounce_closed(sent)

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
            return self._identified(item, backend.sse_hello(item.as_dict()))
        if isinstance(item, StreamSkip):
            return self._identified(item, backend.sse_skip(item.as_dict()))
        if isinstance(item, StreamEnd):
            return backend.sse_end(item.as_dict()).encode("utf-8")
        message_envelope = getattr(item, "envelope", None)
        server_time = int(self._server_clock() * 1000)
        if isinstance(item, Envelope):
            envelope = item
        elif isinstance(message_envelope, Envelope):
            return self._message_frame(
                backend, message_envelope, cast("_TopicMessage", item), server_time
            )
        elif callable(message_envelope):
            envelope = cast("Callable[[], Envelope]", message_envelope)()
        else:
            msg = "SSE patch sources must yield a patch, envelope, or message."
            raise TypeError(msg)
        meta = StreamMeta(server_time=server_time)
        return backend.sse_stream_event(envelope, meta).encode("utf-8")

    def _message_frame(
        self,
        backend: "PartialProtocolBackend",
        envelope: Envelope,
        message: "_TopicMessage",
        server_time: int,
    ) -> bytes:
        """Serialize one topic message with the `id` line the stream gives it.

        A message with seq 0 is unnumbered, so it carries no position and no id.
        """
        frame = backend.sse_stream_event(envelope, StreamMeta.of(message, server_time))
        if message.seq == 0:
            return frame.encode("utf-8")
        return self._identified(message, frame)

    def _identified(self, item: "StreamPosition | StreamHello", frame: str) -> bytes:
        """Prefix a codec frame with the `id` line `event_id` gives it, if any."""
        event_id = self.event_id(item)
        if event_id is not None and _UNSAFE_ID.isdisjoint(event_id):
            frame = f"id: {event_id}\n{frame}"
        return frame.encode("utf-8")

    def event_id(self, frame: "StreamPosition | StreamHello") -> str | None:
        """Return the SSE `id` of a message, skip or hello frame, or None to send none.

        The writer adds it to the codec's frame, so every protocol backend carries it.
        """
        del frame
        return None

    def _announce_opened(self) -> None:
        """Report that streaming starts when iteration produces its first frame.

        A failing receiver is logged, so an observer never kills the stream it watches.
        """
        sender = type(self)
        if sse_stream_opened.has_listeners(sender):
            sse_stream_opened.send_robust(sender=sender, request=self._request)

    async def _aannounce_opened(self) -> None:
        """Report the stream opening from the event loop, sync receivers off it."""
        sender = type(self)
        if sse_stream_opened.has_listeners(sender):
            await sse_stream_opened.asend_robust(sender=sender, request=self._request)

    def _retry_frame(self) -> bytes:
        """Return the leading SSE `retry` hint frame."""
        return f"retry: {self._retry_ms}\n\n".encode()

    def _announce_closed(self, sent: int) -> None:
        """Fire the close signal with the stream's duration and event count."""
        sender = type(self)
        if sse_stream_closed.has_listeners(sender):
            sse_stream_closed.send_robust(sender=sender, **self._closed_kwargs(sent))

    async def _aannounce_closed(self, sent: int) -> None:
        """Fire the close signal from the event loop, sync receivers off it."""
        sender = type(self)
        if sse_stream_closed.has_listeners(sender):
            await sse_stream_closed.asend_robust(
                sender=sender, **self._closed_kwargs(sent)
            )

    def _closed_kwargs(self, sent: int) -> dict[str, object]:
        """Return the close signal arguments, the duration measured now."""
        return {
            "request": self._request,
            "duration_ms": (self._clock() - self._opened_at) * 1000,
            "envelopes_sent": sent,
        }


def _sse_option(option: str) -> object:
    """Return one raw SSE option of the active backend, or None when it is unset."""
    sse = partial_backend_manager.get().options.get(SSE_OPTION)
    return sse.get(option) if isinstance(sse, dict) else None


def retry_ms_valid(value: object) -> TypeGuard[int]:
    """Whether `value` is a usable `RETRY_MS`, a whole number of milliseconds from 0."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def heartbeat_seconds_valid(value: object) -> TypeGuard[float]:
    """Whether `value` is a usable `HEARTBEAT_SECONDS`, a finite number above zero."""
    return positive_number(value, math.inf)


def _retry_ms() -> int:
    """Return the EventSource retry hint, the default for an invalid `RETRY_MS`."""
    value = _sse_option(RETRY_OPTION)
    return value if retry_ms_valid(value) else _DEFAULT_RETRY_MS


def sse_heartbeat_seconds() -> float:
    """Return the SSE heartbeat interval from the active backend options."""
    value = _sse_option(HEARTBEAT_OPTION)
    if heartbeat_seconds_valid(value):
        return float(value)
    return _DEFAULT_HEARTBEAT_SECONDS


class PatchEventStream(PatchStreamWriter):
    """SSE response that emits manually supplied patch envelopes."""

    def __init__(
        self,
        request: "HttpRequest",
        source: "Iterable[Patches | None] | AsyncIterable[Patches | None]",
        *,
        heartbeat_seconds: float | None = None,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        """Build a compatible manual stream from a sync or async patch source."""
        super().__init__(
            request, source, heartbeat_seconds=heartbeat_seconds, clock=clock
        )


__all__ = [
    "HEARTBEAT_OPTION",
    "RETRY_OPTION",
    "SSE_OPTION",
    "PatchEventStream",
    "PatchStreamWriter",
    "heartbeat_seconds_valid",
    "retry_ms_valid",
    "sse_heartbeat_seconds",
]
