import sys
import types
import zlib

import pytest


STDLIB_ZSTD = "compression.zstd"
CODEC_MODULES = (STDLIB_ZSTD, "zstandard", "brotli", "brotlicffi")


def inflate(data: bytes) -> bytes:
    """Decode raw deflate, the stand-in format every fake codec writes."""
    return zlib.decompressobj(-15).decompress(data)


def _deflater(level: int):
    return zlib.compressobj(max(1, min(level, 9)), zlib.DEFLATED, -15)


class FakeBrotliCompressor:
    """The `brotli.Compressor` surface over raw deflate."""

    def __init__(self, quality: int = 11, lgwin: int = 22) -> None:
        """Keep the options the backend passed and open the deflate stream."""
        self.quality = quality
        self.lgwin = lgwin
        self._stream = _deflater(quality)

    def process(self, data: bytes) -> bytes:
        return self._stream.compress(data)

    def flush(self) -> bytes:
        return self._stream.flush(zlib.Z_SYNC_FLUSH)

    def finish(self) -> bytes:
        return self._stream.flush(zlib.Z_FINISH)


class FakeDecompressor:
    """The decoder surface of every fake codec, reading raw deflate."""

    def __init__(self) -> None:
        """Open the inflate stream."""
        self._stream = zlib.decompressobj(-15)

    def decompress(self, data: bytes) -> bytes:
        return self._stream.decompress(data)

    process = decompress

    def decompressobj(self) -> "FakeDecompressor":
        return self


def _brotli_once(data: bytes, quality: int = 11) -> bytes:
    return zlib.compress(data, max(1, min(quality, 9)), wbits=-15)


def fake_brotli(name: str = "brotli") -> types.ModuleType:
    """Return a module with the `brotli` API, compressing as raw deflate."""
    module = types.ModuleType(name)
    module.Compressor = FakeBrotliCompressor
    module.Decompressor = FakeDecompressor
    module.compress = _brotli_once
    return module


class _StdlibParameter:
    compression_level = "compression_level"
    window_log = "window_log"


class FakeStdlibZstdCompressor:
    """The `compression.zstd.ZstdCompressor` surface over raw deflate."""

    CONTINUE = 0
    FLUSH_BLOCK = 1
    FLUSH_FRAME = 2

    def __init__(self, level=None, options=None) -> None:
        """Take the level from `level` or from the options, as the library does."""
        self.options = dict(options or {})
        self.level = level if level is not None else self.options["compression_level"]
        self.frames = 0
        self._open()

    def _open(self) -> None:
        self._stream = _deflater(self.level)

    def compress(self, data: bytes, mode: int = 0) -> bytes:
        out = self._stream.compress(data)
        if mode == self.FLUSH_BLOCK:
            return out + self._stream.flush(zlib.Z_SYNC_FLUSH)
        if mode == self.FLUSH_FRAME:
            return out + self.flush(self.FLUSH_FRAME)
        return out

    def flush(self, mode: int = 2) -> bytes:
        if mode == self.FLUSH_BLOCK:
            return self._stream.flush(zlib.Z_SYNC_FLUSH)
        out = self._stream.flush(zlib.Z_FINISH)
        self.frames += 1
        self._open()
        return out


def fake_stdlib_zstd() -> types.ModuleType:
    """Return a module with the `compression.zstd` API, compressing as raw deflate."""
    module = types.ModuleType(STDLIB_ZSTD)
    module.CompressionParameter = _StdlibParameter
    module.ZstdCompressor = FakeStdlibZstdCompressor
    module.ZstdDecompressor = FakeDecompressor
    return module


class _Parameters:
    def __init__(self, level: int, window_log: int = 0) -> None:
        self.level = level
        self.window_log = window_log

    @classmethod
    def from_level(cls, level: int, **kwargs: int) -> "_Parameters":
        return cls(level, **kwargs)


class _ZstandardStream:
    def __init__(self, level: int) -> None:
        self._stream = _deflater(level)

    def compress(self, data: bytes) -> bytes:
        return self._stream.compress(data)

    def flush(self, mode: int = 0) -> bytes:
        if mode == 1:
            return self._stream.flush(zlib.Z_SYNC_FLUSH)
        return self._stream.flush(zlib.Z_FINISH)


class FakeZstandardCompressor:
    """The `zstandard.ZstdCompressor` surface over raw deflate."""

    def __init__(self, level: int = 3, compression_params=None) -> None:
        """Take the level from `level` or from the parameters, as the library does."""
        self.params = compression_params
        self.level = level if compression_params is None else compression_params.level
        self.streams = 0

    def compressobj(self) -> _ZstandardStream:
        self.streams += 1
        return _ZstandardStream(self.level)

    def compress(self, data: bytes) -> bytes:
        return zlib.compress(data, 9, wbits=-15)


def fake_zstandard() -> types.ModuleType:
    """Return a module with the `zstandard` API, compressing as raw deflate."""
    module = types.ModuleType("zstandard")
    module.COMPRESSOBJ_FLUSH_FINISH = 0
    module.COMPRESSOBJ_FLUSH_BLOCK = 1
    module.ZstdCompressionParameters = _Parameters
    module.ZstdCompressor = FakeZstandardCompressor
    module.ZstdDecompressor = FakeDecompressor
    return module


def install(
    monkeypatch: pytest.MonkeyPatch, **modules: types.ModuleType | None
) -> None:
    """Make each codec module import as given, `None` refusing it, the rest refused.

    Keys spell the module with `_` for `.`, as in `compression_zstd`.
    """
    for name in CODEC_MODULES:
        monkeypatch.setitem(sys.modules, name, modules.get(name.replace(".", "_")))
