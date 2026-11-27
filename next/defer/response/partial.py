"""The streamed answer of a partial request, its deferred boundaries as SSE events."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Iterator
from contextlib import aclosing
from typing import TYPE_CHECKING, ClassVar, Final, override

from django.utils.cache import patch_vary_headers

from next.compression.manager import OPT_OUT, prepare_stream
from next.partial import keys
from next.partial.sse import EventWriter
from next.partial.stream import (
    END_EVENT,
    HELLO_EVENT,
    PATCH_EVENT,
    StreamEnd,
    StreamHello,
    StreamPatch,
)

from .lifetime import Lifetime, StreamGrant
from .pump import aend, apump, astart, end, on_request_thread, pump, start, watched
from .writer import Writer


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from django.http import HttpRequest

    from next.compression.streams import StreamEncoding
    from next.defer.markers import Piece
    from next.partial.envelope import Envelope

    from .chunks import Payloads
    from .lifetime import Batches


COMPLETE: Final = "complete"
"""The end code of a stream whose every boundary settled before the deadline."""

DEADLINE: Final = "deadline"
"""The end code of a stream whose unfinished boundaries settled as `timeout`."""

FAILED: Final = "error"
"""The end code of a stream whose machinery failed after the first byte."""

CONTENT_TYPE: Final = "text/event-stream; charset=utf-8"
CACHE_CONTROL: Final = "private, no-cache, no-store, no-transform"

_VARY: Final = ("Accept-Encoding", "Accept")
_PAD_TO: Final = 1024
_PAD_FRAME: Final = len(":\n\n")
_PING: Final = b": ping\n\n"


def _opening(envelope: Envelope) -> bytes:
    """Return the hello and the envelope, padded for a reader that buffers less."""
    hello = StreamHello(server_time=int(time.time() * 1000), topics={}, streamed=True)
    first = StreamPatch({keys.CHUNK_ENVELOPE: envelope.as_dict()})
    data = frame(HELLO_EVENT, hello.as_dict()) + frame(PATCH_EVENT, first.as_dict())
    if len(data) < _PAD_TO:
        data += b":" + b" " * (_PAD_TO - len(data) - _PAD_FRAME) + b"\n\n"
    return data


def frame(event: str, data: Mapping[str, object]) -> bytes:
    """Return one SSE event whose data is a single line of compact JSON.

    JSON escapes every CR and LF inside a string, so no payload can end the line.
    """
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


class _Events(Writer):
    """The `patch` frames of one partial stream and its `end` frame."""

    __slots__ = ("deadline", "payloads")

    def __init__(self, life: Lifetime, payloads: Payloads, deadline: float) -> None:
        """Write a batch whole, ending at `deadline` for a timed-out boundary."""
        super().__init__(life, split=False)
        self.payloads = payloads
        self.deadline = deadline

    @override
    def frame(self, piece: Piece) -> bytes:
        """Return the `patch` frame of one settled piece."""
        return frame(PATCH_EVENT, self.payloads.payload(piece))

    def end(self) -> bytes:
        """Return the `end` frame, a failure of the stream or its jobs named first."""
        life = self.life
        if life.broken:
            code = FAILED
        elif life.timed_out and time.monotonic() >= self.deadline:
            code = DEADLINE
        else:
            code = COMPLETE
        return frame(END_EVENT, StreamEnd(code).as_dict())


type _Item = bytes | None


class PartialStream(EventWriter[bytes]):
    """A partial response sent as its envelope, a frame per settled boundary, its end.

    It is read once, so it writes no `id:` and no `retry:` field.
    """

    heartbeat_frame: ClassVar[bytes] = _PING

    def __init__(
        self,
        request: HttpRequest,
        envelope: Envelope,
        batches: Batches,
        payloads: Payloads,
        *,
        deadline: float,
        grant: StreamGrant | None = None,
        compress: bool = True,
    ) -> None:
        """Build the opening frames, then hold the jobs and settle headers and coding.

        A first frame that fails to serialise raises here, before any header is sent.
        """
        self._opening = _opening(envelope)
        life = Lifetime(
            request, batches, grant or StreamGrant(), sender=PartialStream, partial=True
        )
        self._events = _Events(life, payloads, deadline)
        self._complete = False
        self._encoding: StreamEncoding | None = None
        self._body: Iterator[bytes] | AsyncIterator[bytes] | None = None
        source = self._aitems() if life.asynchronous else self._items()
        super().__init__(request, source)
        self["Content-Type"] = CONTENT_TYPE
        patch_vary_headers(self, _VARY)
        self._encoding = self._coding(request, compress=compress)

    def _coding(self, request: HttpRequest, *, compress: bool) -> StreamEncoding | None:
        """Negotiate the coding, then mark the stream `no-transform` for intermediaries.

        The stream compresses itself, so its own directive never refuses the coding.
        """
        del self["Cache-Control"]
        if not compress:
            setattr(self, OPT_OUT, False)
        encoding = prepare_stream(request, self)
        self["Cache-Control"] = CACHE_CONTROL
        return encoding

    @override
    def close(self) -> None:
        """Close once, ending a stream its reader left as aborted."""
        if self._finished:
            return
        life = self._events.life
        try:
            body = self._body
            if isinstance(body, AsyncGenerator):
                life.close_later(body)
            super().close()
        finally:
            end(life, aborted=True)

    def discard(self) -> None:
        """End a stream its view never returned, with no `request_finished` fired."""
        self._finished = True
        end(self._events.life, aborted=True)

    @override
    def _build_content(
        self, source: Iterable[bytes | None] | AsyncIterable[bytes | None]
    ) -> Iterator[bytes] | AsyncGenerator[bytes, None]:
        """Return the frames of the writer, each compressed when a coding was agreed."""
        content = super()._build_content(source)
        body = (
            watched(self._events.life, self._acoded(content))
            if isinstance(content, AsyncGenerator)
            else self._coded(content)
        )
        self._body = body
        return body

    def _coded(self, content: Iterator[bytes]) -> Iterator[bytes]:
        encoding = self._encoding
        if encoding is None:
            yield from content
        else:
            yield from encoding.wrap(content)

    async def _acoded(
        self, content: AsyncGenerator[bytes, None]
    ) -> AsyncGenerator[bytes, None]:
        encoding = self._encoding
        coded = content if encoding is None else encoding.awrap(content)
        async with aclosing(coded) as chunks:
            async for chunk in chunks:
                yield chunk

    @override
    def _event_frame(self, item: bytes) -> bytes:
        """Return the frames of one batch, serialised where a failure is contained."""
        return item

    @override
    def _opening_frame(self) -> bytes:
        """Return the hello and the envelope the view already serialised."""
        return self._opening

    @override
    def _announce_opened(self) -> None:
        """Start the stream as its body begins, sending `page_stream_opened`."""
        start(self._events.life)

    @override
    async def _aannounce_opened(self) -> None:
        """Start the stream as its body begins on the server loop."""
        await astart(self._events.life)

    @override
    def _announce_closed(self, sent: int) -> None:
        """End the stream once, aborted unless its end frame was written."""
        end(self._events.life, aborted=not self._complete)

    @override
    async def _aannounce_closed(self, sent: int) -> None:
        """End the stream once from the server loop."""
        await aend(self._events.life, aborted=not self._complete)

    def _items(self) -> Iterator[_Item]:
        """Yield the frames of each batch, then the end, on the request thread.

        An idle batch yields `None`, which the writer sends as a heartbeat.
        """
        events = self._events
        runs = events.life.batches.run(idle=self._heartbeat_seconds)
        try:
            for data in pump(events, runs):
                yield data or None
        finally:
            runs.close()
        owed = events.closing()
        yield owed + events.end()
        self._complete = True

    async def _aitems(self) -> AsyncIterator[_Item]:
        """Yield what `_items` does on the running server loop."""
        events = self._events
        runs = events.life.batches.arun()
        try:
            async for data in apump(events, runs):
                yield data
        finally:
            await runs.aclose()
        owed = await on_request_thread(events.closing)
        yield owed + events.end()
        self._complete = True


__all__ = [
    "CACHE_CONTROL",
    "COMPLETE",
    "CONTENT_TYPE",
    "DEADLINE",
    "FAILED",
    "PartialStream",
    "frame",
]
