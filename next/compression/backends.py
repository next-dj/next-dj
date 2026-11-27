"""The codec contracts and the zstd, brotli and gzip backends that implement them.

A codec whose library does not import stays unavailable, never an import error.
"""

import importlib
import secrets
import struct
import threading
import zlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from types import ModuleType
from typing import ClassVar, Final, Protocol, override

from next.caches import pure_cache

from .errors import CodecUnavailableError


_GZIP_WBITS: Final = 31
_RAW_WBITS: Final = -15
_GZIP_HEAD: Final = b"\x1f\x8b\x08"
_GZIP_TAIL: Final = b"\x00\x00\x00\x00\x00\xff"
_FNAME: Final = b"\x08"
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

    `windows` is `None` for a fixed window, and `extra` names the extra of its library.
    """

    coding: ClassVar[str]
    level: ClassVar[int]
    levels: ClassVar[tuple[int, int]]
    window: ClassVar[int | None] = None
    windows: ClassVar[tuple[int, int] | None] = None
    resets: ClassVar[bool] = True
    extra: ClassVar[str | None] = None

    @abstractmethod
    def available(self) -> bool:
        """Return whether the codec library imports, cached per process."""

    @abstractmethod
    def create(self, level: int, window: int | None, *, pad: int = 0) -> Compressor:
        """Return a fresh compressor for one stream.

        `pad` is the most random bytes the codec adds where its decoders skip them.
        """

    @abstractmethod
    def compress_once(self, data: bytes, level: int, *, pad: int = 0) -> bytes:
        """Compress one buffered body in one shot, padded as `create` pads a stream."""

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


@pure_cache()
def zstd_library() -> ModuleType | None:
    """Return `compression.zstd`, else `zstandard`, else `None`, once per process."""
    return _first_module(_STDLIB_ZSTD, "zstandard")


@pure_cache()
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


class _BrotliStream(Protocol):
    """The calls a `brotli` or `brotlicffi` compressor answers."""

    def process(self, data: bytes, /) -> bytes:
        """Feed `data`, returning the bytes ready so far."""

    def flush(self) -> bytes:
        """Return everything fed so far in a decodable form."""

    def finish(self) -> bytes:
        """End the stream."""


class _ZstdStream(Protocol):
    """The calls a zstd stream of `compression.zstd` or `zstandard` answers."""

    def compress(self, data: bytes, /) -> bytes:
        """Feed `data`, returning the bytes ready so far."""

    def flush(self, mode: int, /) -> bytes:
        """Return what `mode` ends, a block or the whole frame."""


class _ZstdFrames(Protocol):
    """The one-shot call of a reused `compression.zstd` compressor."""

    def compress(self, data: bytes, mode: int, /) -> bytes:
        """Compress `data` ended as `mode` asks, a whole frame here."""


class _Contexts(threading.local):
    """The one-shot compression contexts of one thread, keyed by level."""

    def __init__(self) -> None:
        """Start each thread with no context."""
        self.held: dict[int, OneShot] = {}


def _gzip_header(pad: int) -> bytes:
    """Return a gzip member header, its `FNAME` field a random length up to `pad`.

    Browsers skip the name, so each response length carries noise against BREACH.
    """
    size = secrets.randbelow(pad + 1) if pad > 0 else 0
    if not size:
        return _GZIP_HEAD + b"\x00" + _GZIP_TAIL
    return _GZIP_HEAD + _FNAME + _GZIP_TAIL + b"a" * size + b"\x00"


def _gzip_trailer(crc: int, size: int) -> bytes:
    """Return the CRC-32 and the input size that end a gzip member."""
    return struct.pack("<II", crc, size & 0xFFFFFFFF)


class _GzipCompressor(Compressor):
    """A gzip stream over raw `zlib` deflate, one member per response."""

    __slots__ = ("_crc", "_head", "_size", "_stream")

    def __init__(self, level: int, pad: int) -> None:
        """Open the deflate stream, the header held for the first output."""
        self._stream = zlib.compressobj(level, zlib.DEFLATED, _RAW_WBITS)
        self._head = _gzip_header(pad)
        self._crc = 0
        self._size = 0

    def _out(self, data: bytes) -> bytes:
        head = self._head
        if not head:
            return data
        self._head = b""
        return head + data

    @override
    def compress(self, data: bytes) -> bytes:
        self._crc = zlib.crc32(data, self._crc)
        self._size += len(data)
        return self._out(self._stream.compress(data))

    @override
    def flush(self) -> bytes:
        return self._out(self._stream.flush(zlib.Z_SYNC_FLUSH))

    @override
    def finish(self) -> bytes:
        tail = self._stream.flush(zlib.Z_FINISH)
        return self._out(tail) + _gzip_trailer(self._crc, self._size)

    @override
    def reset(self) -> bytes:
        return self._out(self._stream.flush(zlib.Z_FULL_FLUSH))


class GzipBackend(CompressionBackend):
    """The `gzip` codec on the standard library `zlib`, always available."""

    coding = "gzip"
    level = 4
    levels = (1, 9)

    @override
    def available(self) -> bool:
        return True

    @override
    def create(self, level: int, window: int | None, *, pad: int = 0) -> Compressor:
        return _GzipCompressor(level, pad)

    @override
    def compress_once(self, data: bytes, level: int, *, pad: int = 0) -> bytes:
        body = zlib.compress(data, level, wbits=_RAW_WBITS)
        return _gzip_header(pad) + body + _gzip_trailer(zlib.crc32(data), len(data))

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
        self._stream: _BrotliStream = library.Compressor(**options)

    @override
    def compress(self, data: bytes) -> bytes:
        return self._stream.process(data)

    @override
    def flush(self) -> bytes:
        return self._stream.flush()

    @override
    def finish(self) -> bytes:
        return self._stream.finish()


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
    extra = "brotli"

    @override
    def available(self) -> bool:
        return brotli_library() is not None

    @override
    def create(self, level: int, window: int | None, *, pad: int = 0) -> Compressor:
        return _BrotliCompressor(
            _required(brotli_library(), self.coding), level, window
        )

    @override
    def compress_once(self, data: bytes, level: int, *, pad: int = 0) -> bytes:
        library = _required(brotli_library(), self.coding)
        compressed: bytes = library.compress(data, quality=level)
        return compressed

    @override
    def decoder(self) -> Decoder:
        library = _required(brotli_library(), self.coding)
        decode: Decoder = library.Decompressor().process
        return decode


class _StdlibZstdCompressor(Compressor):
    """A zstd stream over `compression.zstd`, which starts a new frame on reuse."""

    __slots__ = ("_block", "_frame", "_stream")

    def __init__(self, library: ModuleType, level: int, window: int) -> None:
        """Open the stream with the level and the window as compression options."""
        parameter = library.CompressionParameter
        options = {parameter.compression_level: level, parameter.window_log: window}
        self._stream: _ZstdStream = library.ZstdCompressor(options=options)
        self._block: int = library.ZstdCompressor.FLUSH_BLOCK
        self._frame: int = library.ZstdCompressor.FLUSH_FRAME

    @override
    def compress(self, data: bytes) -> bytes:
        return self._stream.compress(data)

    @override
    def flush(self) -> bytes:
        return self._stream.flush(self._block)

    @override
    def finish(self) -> bytes:
        return self._stream.flush(self._frame)

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
        self._stream: _ZstdStream = self._context.compressobj()
        self._block: int = library.COMPRESSOBJ_FLUSH_BLOCK
        self._end: int = library.COMPRESSOBJ_FLUSH_FINISH

    @override
    def compress(self, data: bytes) -> bytes:
        return self._stream.compress(data)

    @override
    def flush(self) -> bytes:
        return self._stream.flush(self._block)

    @override
    def finish(self) -> bytes:
        return self._stream.flush(self._end)

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
        context: _ZstdFrames = library.ZstdCompressor(
            options={
                parameter.compression_level: level,
                parameter.window_log: ZSTD_WINDOW_LIMIT,
            }
        )
        frame: int = library.ZstdCompressor.FLUSH_FRAME

        def stdlib(data: bytes) -> bytes:
            return context.compress(data, frame)

        return stdlib
    params = library.ZstdCompressionParameters.from_level(
        level, window_log=ZSTD_WINDOW_LIMIT
    )
    reused: OneShot = library.ZstdCompressor(compression_params=params).compress
    return reused


class ZstdBackend(CompressionBackend):
    """The `zstd` codec, `compression.zstd` from Python 3.14, the `zstd` extra below."""

    coding = "zstd"
    level = 3
    levels = (1, 22)
    window = 17
    windows = (10, ZSTD_WINDOW_LIMIT)
    extra = "zstd"

    def __init__(self) -> None:
        """Hold no one-shot context until a thread compresses its first body."""
        self._contexts = _Contexts()

    @override
    def available(self) -> bool:
        return zstd_library() is not None

    @override
    def create(self, level: int, window: int | None, *, pad: int = 0) -> Compressor:
        """Return a stream compressor, its window never above `ZSTD_WINDOW_LIMIT`."""
        capped = ZSTD_WINDOW_LIMIT if window is None else min(window, ZSTD_WINDOW_LIMIT)
        return _zstd_stream(_required(zstd_library(), self.coding), level, capped)

    @override
    def compress_once(self, data: bytes, level: int, *, pad: int = 0) -> bytes:
        held = self._contexts.held
        context = held.get(level)
        if context is None:
            library = _required(zstd_library(), self.coding)
            context = held[level] = _zstd_context(library, level)
        return context(data)

    @override
    def decoder(self) -> Decoder:
        library = _required(zstd_library(), self.coding)
        decompressor = library.ZstdDecompressor()
        decode: Decoder = (
            decompressor.decompress
            if library.__name__ == _STDLIB_ZSTD
            else decompressor.decompressobj().decompress
        )
        return decode


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
