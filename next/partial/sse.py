"""SSE bridge streaming patch envelopes over the page render escape hatch."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.http import StreamingHttpResponse

from .headers import set_partial_vary
from .manager import partial_backend_manager
from .signals import sse_stream_closed, sse_stream_opened


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator

    from django.http import HttpRequest

    from .patches import Patches


_EVENT_STREAM = "text/event-stream"
_CACHE_CONTROL = "no-cache, no-transform"
_ACCEL_BUFFERING = "X-Accel-Buffering"
_HEARTBEAT_COMMENT = ": heartbeat\n\n"
_RETRY_OPTION = "RETRY_MS"
_HEARTBEAT_OPTION = "HEARTBEAT_SECONDS"
_SSE_OPTION = "SSE"
_DEFAULT_RETRY_MS = 3000
_DEFAULT_HEARTBEAT_SECONDS = 25.0


class PatchEventStream(StreamingHttpResponse):
    """SSE response that emits patch envelopes as `next-patches` events.

    Django buffers a mismatched sync/async iterator fully before the first byte,
    hanging an infinite stream, so the constructor raises `ImproperlyConfigured`.
    """

    def __init__(
        self,
        request: "HttpRequest",
        source: "Iterable[Patches] | AsyncIterable[Patches]",
        *,
        heartbeat_seconds: float | None = None,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        """Build the stream over a sync or async source of patch builders.

        The heartbeat interval falls back to the active backend's
        `HEARTBEAT_SECONDS` option when no explicit argument is passed.
        """
        self._request = request
        self._clock = clock if clock is not None else time.monotonic
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
        sse_stream_opened.send(sender=type(self), request=request)

    def _guard_source_kind(
        self, source: "Iterable[Patches] | AsyncIterable[Patches]"
    ) -> None:
        """Refuse a source kind the request's server kind would buffer.

        Django reads an async iterator fully under WSGI and a sync one fully under ASGI
        before the first byte, hanging a mismatched stream instead of flushing it.
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
        self, source: "Iterable[Patches] | AsyncIterable[Patches]"
    ) -> "Iterator[bytes] | AsyncIterator[bytes]":
        """Return a sync or async byte stream matching the source kind."""
        if isinstance(source, AsyncIterable):
            return self._async_stream(source)
        return self._sync_stream(source)

    def _sync_stream(self, source: "Iterable[Patches]") -> "Iterator[bytes]":
        """Yield SSE bytes for a sync source, with no heartbeat.

        A blocked `next()` has nothing to interrupt it without a thread, so a quiet sync
        stream sends no heartbeat and a keepalive is the source's own job.
        """
        sent = 0
        yield self._retry_frame()
        try:
            for patches in source:
                yield self._event_frame(patches)
                sent += 1
        finally:
            self._close_source(source)
            self._announce_closed(sent)

    @staticmethod
    def _close_source(source: "Iterable[Patches]") -> None:
        """Close a generator source so a disconnect leaves nothing suspended.

        A plain iterable has no `close`, and closing a spent generator twice is a no-op.
        """
        close = getattr(source, "close", None)
        if callable(close):
            close()

    async def _async_stream(
        self, source: "AsyncIterable[Patches]"
    ) -> "AsyncIterator[bytes]":
        """Yield SSE bytes for an async source, interleaving heartbeats.

        A single pull task is held across heartbeats so the generator is never
        re-entered while a pull is in flight. A pull past `heartbeat_seconds` yields a
        comment frame to keep a buffering proxy connected.
        """
        sent = 0
        yield self._retry_frame()
        iterator = source.__aiter__()
        task: asyncio.Future[Patches] | None = None
        try:
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
                yield self._event_frame(patches)
                sent += 1
        finally:
            await self._cleanup_async(task, iterator)
            self._announce_closed(sent)

    async def _cleanup_async(
        self, task: "asyncio.Future[Patches] | None", iterator: "AsyncIterator[Patches]"
    ) -> None:
        """Cancel an in-flight pull and close the source generator.

        A disconnect throws into the stream mid-pull, so the pending task is cancelled
        and the generator's `aclose` driven, both errors suppressed.
        """
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await aclose()

    def _event_frame(self, patches: "Patches") -> bytes:
        """Serialize one builder's envelope as an SSE event frame."""
        backend = partial_backend_manager.get()
        return backend.sse_event(patches.envelope()).encode("utf-8")

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

    A bool is an int to Python but no interval to a client, so `True` falls back
    instead of travelling as a one-millisecond reconnect.
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


__all__ = ["PatchEventStream"]
