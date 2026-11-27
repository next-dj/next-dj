"""Per-write stream compression, the BREACH padding and the off-loop offload rule."""

import asyncio
import enum
import os
import secrets
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Iterable,
    Iterator,
)
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from .backends import Compressor


EVENT_STREAM: Final = "text/event-stream"

FLUSH_BYTES: Final = 16384
"""The input bytes an async stream compresses before it flushes, however fast."""

OFFLOAD_BYTES: Final = 16384
"""The largest write async code compresses on the event loop, the rest in a thread."""

_SAFE: Final = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._"
_BLANK: Final = b" \t\r\n"


@dataclass(frozen=True, slots=True)
class _Frame:
    """Where a format ignores filler, the bytes around it and the ones it may hold."""

    head: bytes
    tail: bytes
    alphabet: bytes


def _table(characters: bytes) -> bytes:
    return bytes(characters[index % len(characters)] for index in range(256))


_MARKUP: Final = _Frame(b"<!--", b"-->", _table(_SAFE))
_SCRIPT: Final = _Frame(b"/*", b"*/", _table(_SAFE))
_JSON: Final = _Frame(b"", b"", _table(_BLANK))
_FRAMES: Final = MappingProxyType(
    {
        "text/html": _MARKUP,
        "text/xml": _MARKUP,
        "application/xml": _MARKUP,
        "text/css": _SCRIPT,
        "text/javascript": _SCRIPT,
        "application/javascript": _SCRIPT,
        "application/json": _JSON,
        EVENT_STREAM: _Frame(b":", b"\n", _table(_SAFE)),
    }
)
_SUFFIXES: Final = MappingProxyType({"+xml": _MARKUP, "+json": _JSON})


def media_type(content_type: str) -> str:
    """Return the lower-cased media type of a `Content-Type` value, minus parameters."""
    return content_type.partition(";")[0].strip().lower()


def _frame(media: str) -> _Frame | None:
    """Return the filler frame of `media`, by its type or its structured suffix."""
    found = _FRAMES.get(media)
    if found is not None:
        return found
    plus = media.rfind("+")
    return None if plus < 0 else _SUFFIXES.get(media[plus:])


def _filler(frame: _Frame | None, limit: int) -> bytes:
    """Return filler in `frame` holding up to `limit` random characters."""
    if frame is None or limit <= 0:
        return b""
    body = os.urandom(secrets.randbelow(limit + 1)).translate(frame.alphabet)
    return frame.head + body + frame.tail


def padding(content_type: str, limit: int) -> bytes:
    """Return random-length filler the content type ignores, empty for other types.

    A comment of letters, digits, `.` and `_` never ends early, and JSON takes blanks.
    """
    return _filler(_frame(media_type(content_type)), limit)


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
        offload_bytes: int | None = None,
        reset: bool = False,
    ) -> None:
        """Hold the compressor, the padding frame of the content type and the limits."""
        self.coding = coding
        self._compressor = compressor
        self._frame = _frame(media_type(content_type))
        self._limit = pad
        self._offload = OFFLOAD_BYTES if offload_bytes is None else offload_bytes
        self._reset = reset

    def write(self, chunk: bytes) -> bytes:
        """Return `chunk` padded, compressed and flushed, empty for an empty chunk."""
        if not chunk:
            return b""
        compressor = self._compressor
        data = compressor.compress(chunk + _filler(self._frame, self._limit))
        return data + (compressor.reset() if self._reset else compressor.flush())

    def offloads(self, chunk: bytes) -> bool:
        """Whether `chunk` is above the offload limit, so it compresses off the loop."""
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

    async def acoalesce(
        self, chunks: AsyncIterable[bytes], quiet: float
    ) -> AsyncGenerator[bytes, None]:
        """Yield `chunks` encoded, flushed at `FLUSH_BYTES` or after a `quiet` pause.

        The first chunk flushes at once, and unflushed bytes wait `quiet` at most.
        """
        reader = _Reader(aiter(chunks))
        held = 0
        first = True
        try:
            while (chunk := await reader.read(quiet if held else None)) is not None:
                if chunk is _QUIET:
                    held = 0
                    yield self._compressor.flush()
                    continue
                data = await self._afed(chunk)
                held += len(chunk)
                if first or held >= FLUSH_BYTES:
                    data += self._compressor.flush()
                    held = 0
                    first = False
                if data:
                    yield data
            tail = self.end()
            if tail:
                yield tail
        finally:
            await reader.cancel()
            aclose = getattr(chunks, "aclose", None)
            if callable(aclose):
                await aclose()

    async def _afed(self, chunk: bytes) -> bytes:
        compress = self._compressor.compress
        if self.offloads(chunk):
            return await asyncio.to_thread(compress, chunk)
        return compress(chunk)


class _Quiet(enum.Enum):
    """What `_Reader.read` answers for a source that stayed idle past the timeout."""

    TOKEN = enum.auto()


_QUIET: Final = _Quiet.TOKEN


class _Reader:
    """Read the non-empty chunks of an async source, waiting `quiet` seconds if asked.

    A quiet read keeps its pending step, run in the context of the reading task.
    """

    __slots__ = ("source", "step")

    def __init__(self, source: AsyncIterator[bytes]) -> None:
        """Hold the source with no read pending."""
        self.source = source
        self.step: asyncio.Future[bytes | None] | None = None

    async def read(self, quiet: float | None) -> bytes | _Quiet | None:
        """Return the next chunk, `None` at the end, `_QUIET` once `quiet` passes."""
        if self.step is None:
            if quiet is None:
                return await _next(self.source)
            task = asyncio.current_task()
            self.step = asyncio.get_running_loop().create_task(
                _next(self.source), context=task.get_context() if task else None
            )
            if not (await asyncio.wait({self.step}, timeout=quiet))[0]:
                return _QUIET
        step, self.step = self.step, None
        return await step

    async def cancel(self) -> None:
        """Cancel a pending read and wait until it stopped."""
        step = self.step
        if step is not None:
            step.cancel()
            await asyncio.gather(step, return_exceptions=True)


async def _next(source: AsyncIterator[bytes]) -> bytes | None:
    """Return the next non-empty chunk of `source`, `None` once it ends."""
    try:
        chunk = await anext(source)
        while not chunk:
            chunk = await anext(source)
    except StopAsyncIteration:
        return None
    return chunk


__all__ = [
    "EVENT_STREAM",
    "FLUSH_BYTES",
    "OFFLOAD_BYTES",
    "StreamEncoding",
    "media_type",
    "padding",
]
