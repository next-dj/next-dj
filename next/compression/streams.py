"""Per-write stream compression, the BREACH padding and the off-loop offload rule."""

import asyncio
import os
import secrets
from collections.abc import AsyncGenerator, AsyncIterable, Iterable, Iterator
from types import MappingProxyType
from typing import Final

from .backends import Compressor


EVENT_STREAM: Final = "text/event-stream"
_HTML_COMMENT: Final = (b"<!--", b"-->")
_FRAMES: Final = MappingProxyType(
    {
        "text/html": _HTML_COMMENT,
        "application/xhtml+xml": _HTML_COMMENT,
        EVENT_STREAM: (b":", b"\n"),
    }
)
_SAFE: Final = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._"
_ALPHABET: Final = bytes(_SAFE[index % len(_SAFE)] for index in range(256))


def media_type(content_type: str) -> str:
    """Return the lower-cased media type of a `Content-Type` value, minus parameters."""
    return content_type.partition(";")[0].strip().lower()


def _filler(frame: tuple[bytes, bytes] | None, limit: int) -> bytes:
    """Return a comment in `frame` holding up to `limit` random safe characters."""
    if frame is None or limit <= 0:
        return b""
    head, tail = frame
    body = os.urandom(secrets.randbelow(limit + 1)).translate(_ALPHABET)
    return head + body + tail


def padding(content_type: str, limit: int) -> bytes:
    """Return a random-length comment the content type ignores, empty for other types.

    Letters, digits, `.` and `_` fill it, so an HTML or SSE comment never ends early.
    """
    return _filler(_FRAMES.get(media_type(content_type)), limit)


class StreamEncoding:
    """One negotiated coding of one stream, each write padded, compressed and flushed.

    A resetting encoding drops the window after each write, so no write shares history.
    """

    __slots__ = ("_compressor", "_frame", "_limit", "_offload", "_reset", "coding")

    def __init__(
        self,
        coding: str,
        compressor: Compressor,
        *,
        content_type: str = "",
        pad: int = 0,
        offload_bytes: int = 0,
        reset: bool = False,
    ) -> None:
        """Hold the compressor, the padding frame of the content type and the limits."""
        self.coding = coding
        self._compressor = compressor
        self._frame = _FRAMES.get(media_type(content_type))
        self._limit = pad
        self._offload = offload_bytes
        self._reset = reset

    def write(self, chunk: bytes) -> bytes:
        """Return `chunk` padded, compressed and flushed, empty for an empty chunk."""
        if not chunk:
            return b""
        compressor = self._compressor
        data = compressor.compress(chunk + _filler(self._frame, self._limit))
        return data + (compressor.reset() if self._reset else compressor.flush())

    def offloads(self, chunk: bytes) -> bool:
        """Whether `chunk` is above `OFFLOAD_BYTES`, so it compresses off the loop."""
        return len(chunk) > self._offload

    async def awrite(self, chunk: bytes, *, offload: bool | None = None) -> bytes:
        """Return `write(chunk)`, compressed in a worker thread when it offloads.

        `offload` forces the choice, the shell of a document always going to a thread.
        """
        away = self.offloads(chunk) if offload is None else offload
        if away:
            return await asyncio.to_thread(self.write, chunk)
        return self.write(chunk)

    def end(self) -> bytes:
        """Return the bytes that end the stream."""
        return self._compressor.finish()

    def wrap(self, chunks: Iterable[bytes]) -> Iterator[bytes]:
        """Yield each chunk encoded, then the end, closing `chunks` however it stops."""
        try:
            for chunk in chunks:
                data = self.write(chunk)
                if data:
                    yield data
            tail = self.end()
            if tail:
                yield tail
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                close()

    async def awrap(self, chunks: AsyncIterable[bytes]) -> AsyncGenerator[bytes, None]:
        """Yield each chunk encoded off the loop past the limit, closing `chunks`."""
        try:
            async for chunk in chunks:
                data = await self.awrite(chunk)
                if data:
                    yield data
            tail = self.end()
            if tail:
                yield tail
        finally:
            aclose = getattr(chunks, "aclose", None)
            if callable(aclose):
                await aclose()


__all__ = ["EVENT_STREAM", "StreamEncoding", "media_type", "padding"]
