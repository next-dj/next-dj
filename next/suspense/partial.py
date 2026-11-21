"""The streamed answer of a partial request, its deferred boundaries as SSE events."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, override

from django.utils.cache import patch_vary_headers

from next.compression import OPT_OUT, prepare_stream
from next.defer import ErrorKind
from next.partial import keys
from next.partial.sse import PatchStreamWriter
from next.partial.stream import (
    END_EVENT,
    HELLO_EVENT,
    PATCH_EVENT,
    StreamEnd,
    StreamHello,
    StreamPatch,
)
from next.suspense.response import Admitted, Lifetime


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest

    from next.compression import StreamEncoding
    from next.partial.envelope import Envelope
    from next.partial.sse import EventItem, PatchSource
    from next.suspense.response import Batches, Payloads, Piece


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


@dataclass(frozen=True, slots=True)
class _Frames(StreamPatch):
    """The `patch` frames of one batch, serialised where a failure is contained."""

    frames: bytes = b""


type _Item = _Frames | None


class PartialStream(PatchStreamWriter):
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
        admitted: Admitted | None = None,
        compress: bool = True,
    ) -> None:
        """Build the opening frames, then hold the jobs and settle headers and coding.

        A first frame that fails to serialise raises here, before any header is sent.
        """
        self._opening = _opening(envelope)
        self._batches = batches
        self._payloads = payloads
        self._deadline = deadline
        self._life = Lifetime(
            request, batches, admitted or Admitted(), sender=PartialStream, partial=True
        )
        self._complete = False
        self._timed_out = False
        self._encoding: StreamEncoding | None = None
        self._body: Iterator[bytes] | AsyncIterator[bytes] | None = None
        source = self._aitems() if self._life.asynchronous else self._items()
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
        life = self._life
        try:
            body = self._body
            if isinstance(body, AsyncGenerator):
                life.close_later(body)
            super().close()
        finally:
            life.end(aborted=True)

    def discard(self) -> None:
        """End a stream its view never returned, with no `request_finished` fired."""
        self._finished = True
        self._life.end(aborted=True)

    @override
    def _build_content(
        self, source: PatchSource
    ) -> Iterator[bytes] | AsyncGenerator[bytes, None]:
        """Return the frames of the writer, each compressed when a coding was agreed."""
        content = super()._build_content(source)
        body = (
            self._acoded(content)
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
    def _opening_frame(self) -> bytes:
        """Return the hello and the envelope the view already serialised."""
        return self._opening

    @override
    def _event_frame(self, item: EventItem) -> bytes:
        """Serialize a frame of the partial wire, any other item as the writer does."""
        if isinstance(item, _Frames):
            return item.frames
        return super()._event_frame(item)

    @override
    def _announce_opened(self) -> None:
        """Start the stream as its body begins, sending `page_stream_opened`."""
        self._life.start()

    @override
    async def _aannounce_opened(self) -> None:
        """Start the stream as its body begins on the server loop."""
        await self._life.astart()

    @override
    def _announce_closed(self, sent: int) -> None:
        """End the stream once, aborted unless its end frame was written."""
        del sent
        self._life.end(aborted=not self._complete)

    @override
    async def _aannounce_closed(self, sent: int) -> None:
        """End the stream once from the server loop."""
        del sent
        await self._life.aend(aborted=not self._complete)

    def _items(self) -> Iterator[_Item]:
        """Yield the frames of each batch, then the end, on the request thread.

        An idle batch yields `None`, which the writer sends as a heartbeat.
        """
        runs = self._batches.run(idle=self._heartbeat_seconds)
        try:
            while (frames := self._next(runs)) is not None:
                yield _Frames({}, frames) if frames else None
        finally:
            runs.close()
        life = self._life
        life.released()
        owed = life.owed(self._frame) if life.broken else b""
        yield _Frames({}, owed + self._end())
        self._complete = True

    async def _aitems(self) -> AsyncIterator[_Item]:
        """Yield what `_items` does on the running server loop."""
        runs = self._batches.arun()
        try:
            while (frames := await self._anext(runs)) is not None:
                yield _Frames({}, frames)
        finally:
            await runs.aclose()
        life = self._life
        await life.areleased()
        owed = await life.aowed(self._frame) if life.broken else b""
        yield _Frames({}, owed + self._end())
        self._complete = True

    def _next(self, runs: Iterator[tuple[Piece, ...]]) -> bytes | None:
        """Return the frames of the next batch, `None` once the jobs ran out."""
        life = self._life
        frames = None
        with life.guard():
            pieces = next(runs, None)
            if pieces is not None:
                self._count(pieces)
                frames = life.frames(pieces, self._frame)
        return frames

    async def _anext(self, runs: AsyncIterator[tuple[Piece, ...]]) -> bytes | None:
        """Return the frames of the next batch on the server loop."""
        life = self._life
        frames = None
        with life.guard():
            pieces = await anext(runs, None)
            if pieces is not None:
                self._count(pieces)
                frames = await life.aframes(pieces, self._frame)
        return frames

    def _frame(self, piece: Piece) -> bytes:
        """Return the `patch` frame of one settled piece."""
        return frame(PATCH_EVENT, self._payloads.written(piece))

    def _count(self, pieces: tuple[Piece, ...]) -> None:
        """Count one batch, noting whether a boundary of it timed out."""
        self._life.count(pieces)
        timed_out = any(piece.outcome.kind is ErrorKind.TIMEOUT for piece in pieces)
        self._timed_out = self._timed_out or timed_out

    def _end(self) -> bytes:
        """Return the `end` frame, after the error frames a failure left owed."""
        return frame(END_EVENT, StreamEnd(self._code()).as_dict())

    def _code(self) -> str:
        """Return how the stream ends, a failure of the stream or its jobs first."""
        if self._life.broken:
            return FAILED
        if self._timed_out and time.monotonic() >= self._deadline:
            return DEADLINE
        return COMPLETE


__all__ = [
    "CACHE_CONTROL",
    "COMPLETE",
    "CONTENT_TYPE",
    "DEADLINE",
    "FAILED",
    "PartialStream",
    "frame",
]
