"""The streamed page response, its shell, a chunk per settled boundary and its tail."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast, override

from django.http import StreamingHttpResponse
from django.utils.cache import patch_cache_control, patch_vary_headers

from next.compression.manager import prepare_stream
from next.pages.responses import unshare

from .lifetime import Lifetime, StreamGrant
from .pump import aend, apump, astart, end, on_request_thread, pump, start, watched
from .writer import Writer


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

    from django.http import HttpRequest

    from next.compression.streams import StreamEncoding
    from next.defer.markers import Piece

    from .chunks import Chunks
    from .document import Document
    from .lifetime import Batches


_PAD_TO: Final = 1024
_PAD_FRAME: Final = len("<!---->")
_FRESHNESS: Final = frozenset(
    {
        "public",
        "private",
        "no-cache",
        "max-age",
        "s-maxage",
        "stale-while-revalidate",
        "stale-if-error",
        "immutable",
    }
)
_DROPPED: Final = ("ETag", "Last-Modified", "Content-Length")
_VARY: Final = ("Accept-Encoding", "Accept")


def _directive(part: str) -> str:
    """Return the lower-cased name of one `Cache-Control` directive."""
    return part.split("=", 1)[0].strip().lower()


class PageWriter(Writer):
    """The frames of one page stream, its shell head first and its tail last.

    It is kept apart from the response, so the body never holds its response.
    """

    __slots__ = ("chunks", "encoding", "head", "tail")

    def __init__(self, life: Lifetime, document: Document, chunks: Chunks) -> None:
        """Hold the shell encoded, so text no byte spells fails before the headers."""
        head, tail = document.head.encode(), document.tail.encode()
        super().__init__(life, split=True)
        self.head: bytes | None = head
        self.tail = tail
        self.chunks = chunks
        self.encoding: StreamEncoding | None = None

    @override
    def frame(self, piece: Piece) -> bytes:
        """Return the chunk script of one piece."""
        return self.chunks.script(piece).encode()

    def first(self) -> bytes:
        """Return the shell up to the split, padded for a browser that buffers less."""
        data = self.head or b""
        self.head = None
        if len(data) < _PAD_TO:
            data += b"<!--" + b" " * max(0, _PAD_TO - len(data) - _PAD_FRAME) + b"-->"
        return data

    def last(self) -> bytes:
        """Return the end marker and the rest of the page."""
        end_script = ""
        with self.life.guard():
            end_script = self.chunks.end()
        data = end_script.encode() + self.tail
        self.tail = b""
        return data

    def encode(self, data: bytes) -> bytes:
        """Return `data` compressed and flushed when the stream has a coding."""
        encoding = self.encoding
        return data if encoding is None else encoding.write(data)

    async def aencode(self, data: bytes, *, offload: bool | None = None) -> bytes:
        """Return `encode(data)`, a write past `OFFLOAD_BYTES` compressed off the loop.

        `offload` forces the choice, as the shell always compresses in a worker thread.
        """
        encoding = self.encoding
        if encoding is None:
            return data
        return await encoding.awrite(data, offload=offload)

    def coded_end(self) -> bytes:
        """Return the bytes that end the coded stream, none for a plain one."""
        encoding = self.encoding
        return b"" if encoding is None else encoding.end()

    def body(self) -> Iterator[bytes]:
        """Yield the shell, a write per batch, then the end and the tail."""
        life = self.life
        try:
            start(life)
            yield self.encode(self.first())
            runs = life.batches.run()
            try:
                for data in pump(self, runs):
                    yield self.encode(data)
            finally:
                runs.close()
            owed = self.closing()
            yield self.encode(owed + self.last()) + self.coded_end()
            end(life, aborted=False)
        finally:
            end(life, aborted=True)

    async def abody(self) -> AsyncGenerator[bytes, None]:
        """Yield what `body` does on the running server loop."""
        life = self.life
        try:
            await astart(life)
            yield await self.aencode(self.first(), offload=True)
            runs = life.batches.arun()
            try:
                async for data in apump(self, runs):
                    yield await self.aencode(data)
            finally:
                await runs.aclose()
            owed = await on_request_thread(self.closing)
            yield await self.aencode(owed + self.last()) + self.coded_end()
            await aend(life, aborted=False)
        finally:
            await aend(life, aborted=True)


class StreamingPageResponse(StreamingHttpResponse):
    """A page sent as its shell, a chunk per settled boundary, then its closing tags.

    The body is produced while it is read, and the response owns its stream slot.
    """

    def __init__(
        self,
        request: HttpRequest,
        document: Document,
        batches: Batches,
        chunks: Chunks,
        *,
        grant: StreamGrant | None = None,
    ) -> None:
        """Hold the shell, the jobs and the writer, writing nothing yet."""
        life = Lifetime(
            request, batches, grant or StreamGrant(), sender=StreamingPageResponse
        )
        writer = PageWriter(life, document, chunks)
        self._request = request
        self._writer = writer
        self._closing = False
        body: Iterator[bytes] | AsyncIterator[bytes] = (
            watched(life, writer.abody()) if life.asynchronous else writer.body()
        )
        self._content = body
        super().__init__(body)

    def settle(self) -> None:
        """Make the stream private and revalidated, then negotiate its coding.

        A stricter directive the page set, such as `no-store` or `no-transform`, stays,
        and the coding is negotiated once the final `Cache-Control` is known.
        """
        kept = [
            part.strip()
            for part in self.get("Cache-Control", "").split(",")
            if part.strip() and _directive(part) not in _FRESHNESS
        ]
        self["Cache-Control"] = ", ".join(kept)
        patch_cache_control(self, private=True, no_cache=True)
        unshare(self)
        for name in _DROPPED:
            if self.has_header(name):
                del self[name]
        self["X-Accel-Buffering"] = "no"
        patch_vary_headers(self, _VARY)
        self._writer.encoding = prepare_stream(self._request, self)

    @override
    def close(self) -> None:
        """Close once, ending a stream its reader left as aborted."""
        if self._closing:
            return
        self._closing = True
        life = self._writer.life
        try:
            if life.asynchronous:
                life.close_later(cast("AsyncGenerator[bytes, None]", self._content))
            super().close()
        finally:
            end(life, aborted=True)

    def discard(self) -> None:
        """End a stream its view never returned, with no `request_finished` fired."""
        self._closing = True
        end(self._writer.life, aborted=True)


__all__ = ["PageWriter", "StreamingPageResponse"]
