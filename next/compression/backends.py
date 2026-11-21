"""The codec contracts and the zstd, brotli and gzip backends that implement them.

A codec whose library does not import stays unavailable, never an import error.
"""

import functools
import importlib
import threading
import zlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from types import ModuleType
from typing import ClassVar, Final, cast, override

from .errors import CodecUnavailableError


_GZIP_WBITS: Final = 31
_STDLIB_ZSTD: Final = "compression.zstd"

ZSTD_WINDOW_LIMIT: Final = 23
"""The largest zstd `windowLog` the `zstd` content coding allows, 8 MB."""

type OneShot = Callable[[bytes], bytes]

type Decoder = Callable[[bytes], bytes]
"""Decode one stream write by write, each call answering what it can so far."""


class Compressor(ABC):
    """One stream of one codec, fed chunk by chunk and flushed per write."""

    __slots__ = ()

    @abstractmethod
    def compress(self, data: bytes) -> bytes:
        """Feed data and return the bytes ready to send, possibly empty."""

    @abstractmethod
    def flush(self) -> bytes:
        """Return everything fed so far in a decodable form."""

    @abstractmethod
    def finish(self) -> bytes:
        """End the stream."""

    def reset(self) -> bytes:
        """Return everything fed so far decodable, later data sharing no history."""
        msg = f"{type(self).__name__} cannot drop its window mid-stream."
        raise NotImplementedError(msg)


class CompressionBackend(ABC):
    """One codec, its content-coding token, its ranges and the compressors it builds.

    `windows` is `None` for a fixed window, and `optional` marks an extra's codec.
    """

    coding: ClassVar[str]
    level: ClassVar[int]
    levels: ClassVar[tuple[int, int]]
    window: ClassVar[int | None] = None
    windows: ClassVar[tuple[int, int] | None] = None
    resets: ClassVar[bool] = True
    optional: ClassVar[bool] = False

    @abstractmethod
    def available(self) -> bool:
        """Return whether the codec library imports, cached per process."""

    @abstractmethod
    def create(self, level: int, window: int | None) -> Compressor:
        """Return a fresh compressor for one stream."""

    @abstractmethod
    def compress_once(self, data: bytes, level: int) -> bytes:
        """Compress one buffered body with a reused per-thread context."""

    def decoder(self) -> Decoder:
        """Return a fresh decoder of one stream or body, for tests and tools."""
        msg = f"{type(self).__name__} offers no decoder for {self.coding!r}."
        raise NotImplementedError(msg)


def _first_module(*names: str) -> ModuleType | None:
    """Return the first of `names` that imports, `None` when none does."""
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


@functools.cache
def zstd_library() -> ModuleType | None:
    """Return `compression.zstd`, else `zstandard`, else `None`, once per process."""
    return _first_module(_STDLIB_ZSTD, "zstandard")


@functools.cache
def brotli_library() -> ModuleType | None:
    """Return `brotli`, else `brotlicffi`, else `None`, once per process."""
    return _first_module("brotli", "brotlicffi")


def forget_codec_libraries() -> None:
    """Drop the memoised codec libraries, so the next probe imports again."""
    zstd_library.cache_clear()
    brotli_library.cache_clear()


def _required(library: ModuleType | None, token: str) -> ModuleType:
    """Return `library`, raising `CodecUnavailableError` when it is missing."""
    if library is None:
        raise CodecUnavailableError(token)
    return library


class _Contexts(threading.local):
    """The one-shot compression contexts of one thread, keyed by level."""

    def __init__(self) -> None:
        """Start each thread with no context."""
        self.held: dict[int, OneShot] = {}


class _GzipCompressor(Compressor):
    """A gzip stream over `zlib`, one member per response."""

    __slots__ = ("_stream",)

    def __init__(self, level: int) -> None:
        """Open the deflate stream with the gzip header and trailer."""
        self._stream = zlib.compressobj(level, zlib.DEFLATED, _GZIP_WBITS)

    @override
    def compress(self, data: bytes) -> bytes:
        return self._stream.compress(data)

    @override
    def flush(self) -> bytes:
        return self._stream.flush(zlib.Z_SYNC_FLUSH)

    @override
    def finish(self) -> bytes:
        return self._stream.flush(zlib.Z_FINISH)

    @override
    def reset(self) -> bytes:
        return self._stream.flush(zlib.Z_FULL_FLUSH)


class GzipBackend(CompressionBackend):
    """The `gzip` codec on the standard library `zlib`, always available."""

    coding = "gzip"
    level = 4
    levels = (1, 9)

    @override
    def available(self) -> bool:
        return True

    @override
    def create(self, level: int, window: int | None) -> Compressor:
        return _GzipCompressor(level)

    @override
    def compress_once(self, data: bytes, level: int) -> bytes:
        return zlib.compress(data, level, wbits=_GZIP_WBITS)

    @override
    def decoder(self) -> Decoder:
        return zlib.decompressobj(_GZIP_WBITS).decompress


class _BrotliCompressor(Compressor):
    """A brotli stream over `brotli` or `brotlicffi`, which share one API."""

    __slots__ = ("_stream",)

    def __init__(self, library: ModuleType, level: int, window: int | None) -> None:
        """Open the stream, the library default window when `window` is `None`."""
        options: dict[str, int] = {"quality": level}
        if window is not None:
            options["lgwin"] = window
        self._stream = library.Compressor(**options)

    @override
    def compress(self, data: bytes) -> bytes:
        return cast("bytes", self._stream.process(data))

    @override
    def flush(self) -> bytes:
        return cast("bytes", self._stream.flush())

    @override
    def finish(self) -> bytes:
        return cast("bytes", self._stream.finish())


class BrotliBackend(CompressionBackend):
    """The `br` codec, available once the `brotli` extra is installed.

    Brotli cannot drop its window mid-stream, so a resetting stream never picks it.
    """

    coding = "br"
    level = 4
    levels = (0, 11)
    window = 18
    windows = (10, 24)
    resets = False
    optional = True

    @override
    def available(self) -> bool:
        return brotli_library() is not None

    @override
    def create(self, level: int, window: int | None) -> Compressor:
        return _BrotliCompressor(
            _required(brotli_library(), self.coding), level, window
        )

    @override
    def compress_once(self, data: bytes, level: int) -> bytes:
        library = _required(brotli_library(), self.coding)
        return cast("bytes", library.compress(data, quality=level))

    @override
    def decoder(self) -> Decoder:
        library = _required(brotli_library(), self.coding)
        return cast("Decoder", library.Decompressor().process)


class _StdlibZstdCompressor(Compressor):
    """A zstd stream over `compression.zstd`, which starts a new frame on reuse."""

    __slots__ = ("_block", "_frame", "_stream")

    def __init__(self, library: ModuleType, level: int, window: int) -> None:
        """Open the stream with the level and the window as compression options."""
        parameter = library.CompressionParameter
        options = {parameter.compression_level: level, parameter.window_log: window}
        self._stream = library.ZstdCompressor(options=options)
        self._block = library.ZstdCompressor.FLUSH_BLOCK
        self._frame = library.ZstdCompressor.FLUSH_FRAME

    @override
    def compress(self, data: bytes) -> bytes:
        return cast("bytes", self._stream.compress(data))

    @override
    def flush(self) -> bytes:
        return cast("bytes", self._stream.flush(self._block))

    @override
    def finish(self) -> bytes:
        return cast("bytes", self._stream.flush(self._frame))

    @override
    def reset(self) -> bytes:
        return self.finish()


class _ZstandardCompressor(Compressor):
    """A zstd stream over `zstandard`, whose finished object opens a new frame."""

    __slots__ = ("_block", "_context", "_end", "_stream")

    def __init__(self, library: ModuleType, level: int, window: int) -> None:
        """Open the stream with parameters derived from the level and the window."""
        params = library.ZstdCompressionParameters.from_level(level, window_log=window)
        self._context = library.ZstdCompressor(compression_params=params)
        self._stream = self._context.compressobj()
        self._block = library.COMPRESSOBJ_FLUSH_BLOCK
        self._end = library.COMPRESSOBJ_FLUSH_FINISH

    @override
    def compress(self, data: bytes) -> bytes:
        return cast("bytes", self._stream.compress(data))

    @override
    def flush(self) -> bytes:
        return cast("bytes", self._stream.flush(self._block))

    @override
    def finish(self) -> bytes:
        return cast("bytes", self._stream.flush(self._end))

    @override
    def reset(self) -> bytes:
        tail = self.finish()
        self._stream = self._context.compressobj()
        return tail


def _zstd_stream(library: ModuleType, level: int, window: int) -> Compressor:
    """Return the zstd compressor of whichever library imported."""
    if library.__name__ == _STDLIB_ZSTD:
        return _StdlibZstdCompressor(library, level, window)
    return _ZstandardCompressor(library, level, window)


def _zstd_context(library: ModuleType, level: int) -> OneShot:
    """Return a reusable one-shot zstd compression of one whole frame.

    The window is capped, as a high level alone would ask a browser for more than 8 MB.
    """
    if library.__name__ == _STDLIB_ZSTD:
        parameter = library.CompressionParameter
        context = library.ZstdCompressor(
            options={
                parameter.compression_level: level,
                parameter.window_log: ZSTD_WINDOW_LIMIT,
            }
        )
        frame = library.ZstdCompressor.FLUSH_FRAME

        def stdlib(data: bytes) -> bytes:
            return cast("bytes", context.compress(data, frame))

        return stdlib
    params = library.ZstdCompressionParameters.from_level(
        level, window_log=ZSTD_WINDOW_LIMIT
    )
    reused = library.ZstdCompressor(compression_params=params)

    def zstandard(data: bytes) -> bytes:
        return cast("bytes", reused.compress(data))

    return zstandard


class ZstdBackend(CompressionBackend):
    """The `zstd` codec, `compression.zstd` from Python 3.14 and `zstandard` below."""

    coding = "zstd"
    level = 3
    levels = (1, 22)
    window = 17
    windows = (10, ZSTD_WINDOW_LIMIT)

    def __init__(self) -> None:
        """Hold no one-shot context until a thread compresses its first body."""
        self._contexts = _Contexts()

    @override
    def available(self) -> bool:
        return zstd_library() is not None

    @override
    def create(self, level: int, window: int | None) -> Compressor:
        """Return a stream compressor, its window never above `ZSTD_WINDOW_LIMIT`."""
        capped = ZSTD_WINDOW_LIMIT if window is None else min(window, ZSTD_WINDOW_LIMIT)
        return _zstd_stream(_required(zstd_library(), self.coding), level, capped)

    @override
    def compress_once(self, data: bytes, level: int) -> bytes:
        held = self._contexts.held
        context = held.get(level)
        if context is None:
            library = _required(zstd_library(), self.coding)
            context = held[level] = _zstd_context(library, level)
        return context(data)

    @override
    def decoder(self) -> Decoder:
        library = _required(zstd_library(), self.coding)
        if library.__name__ == _STDLIB_ZSTD:
            return cast("Decoder", library.ZstdDecompressor().decompress)
        return cast("Decoder", library.ZstdDecompressor().decompressobj().decompress)


__all__ = [
    "ZSTD_WINDOW_LIMIT",
    "BrotliBackend",
    "CompressionBackend",
    "Compressor",
    "Decoder",
    "GzipBackend",
    "ZstdBackend",
    "brotli_library",
    "forget_codec_libraries",
    "zstd_library",
]
