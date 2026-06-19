"""SSE bridge streaming patch envelopes over the page render escape hatch."""

import asyncio
import time
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse

from .headers import set_partial_vary
from .manager import partial_backend_manager
from .signals import sse_stream_closed, sse_stream_opened


if TYPE_CHECKING:
    from collections.abc import (
        AsyncIterator,
        Callable,
        Iterable,
        Iterator,
    )

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

    Each envelope yielded by the source travels as one `next-patches`
    event serialized by the active protocol backend, the same shape an
    HTTP partial response carries. A sync source streams envelopes as
    they arrive with no heartbeat, a blocked `next()` having nothing to
    interrupt it without a thread. An async source under ASGI interleaves
    heartbeat comments during quiet periods through `asyncio.wait`. The
    politeness headers and the leading `retry` hint are set on
    construction so a buffering proxy or GZipMiddleware does not eat the
    flush. The `sse_stream_opened` signal fires on construction and
    `sse_stream_closed` fires when the stream ends.
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
        content = self._build_content(source)
        super().__init__(content, content_type=_EVENT_STREAM)
        self["Cache-Control"] = _CACHE_CONTROL
        self[_ACCEL_BUFFERING] = "no"
        set_partial_vary(self)
        if sse_stream_opened.receivers:
            sse_stream_opened.send(sender=type(self), request=request)

    def _build_content(
        self,
        source: "Iterable[Patches] | AsyncIterable[Patches]",
    ) -> "Iterator[bytes] | AsyncIterator[bytes]":
        """Return a sync or async byte stream matching the source kind."""
        if isinstance(source, AsyncIterable):
            return self._async_stream(source)
        return self._sync_stream(source)

    def _sync_stream(self, source: "Iterable[Patches]") -> "Iterator[bytes]":
        """Yield SSE bytes for a sync source, with no heartbeat.

        A blocked `next()` on a sync source has nothing to interrupt it
        without a thread, so a quiet sync stream sends no heartbeat. A
        keepalive is the source's own job under WSGI. The close signal
        fires once the source is exhausted or the client disconnects.
        """
        sent = 0
        yield self._retry_frame()
        try:
            for patches in source:
                yield self._event_frame(patches)
                sent += 1
        finally:
            self._announce_closed(sent)

    async def _async_stream(
        self,
        source: "AsyncIterable[Patches]",
    ) -> "AsyncIterator[bytes]":
        """Yield SSE bytes for an async source, interleaving heartbeats.

        A single pull task is held across heartbeats so the source
        generator is never re-entered while a pull is in flight and no
        envelope is lost. A pull that outlasts `heartbeat_seconds`
        yields a comment frame so a buffering proxy keeps the connection.
        The close signal fires once the source is exhausted or the client
        disconnects.
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
            self._announce_closed(sent)

    def _event_frame(self, patches: "Patches") -> bytes:
        """Serialize one builder's envelope as an SSE event frame."""
        backend = partial_backend_manager.get()
        return backend.sse_event(patches.envelope()).encode("utf-8")

    def _retry_frame(self) -> bytes:
        """Return the leading SSE `retry` hint frame."""
        return f"retry: {self._retry_ms}\n\n".encode()

    def _announce_closed(self, sent: int) -> None:
        """Fire the close signal with the stream's duration and event count."""
        if not sse_stream_closed.receivers:
            return
        duration_ms = (self._clock() - self._opened_at) * 1000
        sse_stream_closed.send(
            sender=type(self),
            request=self._request,
            duration_ms=duration_ms,
            envelopes_sent=sent,
        )


def _sse_options() -> dict[str, object]:
    """Return the active backend's SSE options sub-mapping, or an empty one."""
    sse = partial_backend_manager.get().options.get(_SSE_OPTION)
    return sse if isinstance(sse, dict) else {}


def _retry_ms() -> int:
    """Return the EventSource retry hint from the active backend options."""
    value = _sse_options().get(_RETRY_OPTION, _DEFAULT_RETRY_MS)
    if isinstance(value, int):
        return value
    return _DEFAULT_RETRY_MS


def _heartbeat_seconds() -> float:
    """Return the heartbeat interval from the active backend options."""
    value = _sse_options().get(_HEARTBEAT_OPTION, _DEFAULT_HEARTBEAT_SECONDS)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return _DEFAULT_HEARTBEAT_SECONDS


__all__ = ["PatchEventStream"]
