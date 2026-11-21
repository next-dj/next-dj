import zlib

import pytest

from next.compression import (
    BrotliBackend,
    CodecUnavailableError,
    CompressionBackend,
    Compressor,
    GzipBackend,
    ZstdBackend,
)
from next.compression.backends import ZSTD_WINDOW_LIMIT, brotli_library, zstd_library
from tests.support.compressors import (
    fake_brotli,
    fake_stdlib_zstd,
    fake_zstandard,
    inflate,
    install,
)


PARTS = (b"<main>" + b"shell " * 200, b"<p>first boundary</p>", b"<p>second</p></main>")


def _gzip_reader():
    return zlib.decompressobj(31)


def _inflate_reader():
    return zlib.decompressobj(-15)


def _round_trip(compressor: Compressor, reader) -> None:
    """Every flush decodes to exactly the parts fed so far, the end closes the stream."""
    seen = b""
    for part in PARTS:
        seen += reader.decompress(compressor.compress(part) + compressor.flush())
        assert seen.endswith(part)
    seen += reader.decompress(compressor.finish())
    assert seen == b"".join(PARTS)


class TestGzipBackend:
    """`GzipBackend` writes one gzip member, decodable after every flush."""

    def test_always_available(self) -> None:
        assert GzipBackend().available() is True

    def test_a_stream_decodes_after_each_flush(self) -> None:
        reader = _gzip_reader()
        _round_trip(GzipBackend().create(4, None), reader)
        assert reader.eof
        assert reader.unused_data == b""

    def test_one_member_per_stream(self) -> None:
        compressor = GzipBackend().create(4, None)
        body = b"".join(compressor.compress(p) + compressor.flush() for p in PARTS)
        body += compressor.finish()
        assert body.count(b"\x1f\x8b\x08") == 1

    def test_reset_keeps_the_stream_decodable(self) -> None:
        compressor = GzipBackend().create(4, None)
        reader = _gzip_reader()
        out = reader.decompress(compressor.compress(b"secret") + compressor.reset())
        assert out == b"secret"

    def test_compress_once_is_one_member(self) -> None:
        body = b"<html>" + b"x" * 4000
        assert zlib.decompress(GzipBackend().compress_once(body, 4), 31) == body

    def test_the_contract_ranges(self) -> None:
        assert (GzipBackend.coding, GzipBackend.level) == ("gzip", 4)
        assert GzipBackend.windows is None
        assert GzipBackend.resets is True


class TestBrotliBackend:
    """`BrotliBackend` adapts `brotli` and `brotlicffi`, unavailable without both."""

    def test_unavailable_without_a_library(self, monkeypatch) -> None:
        install(monkeypatch)
        backend = BrotliBackend()
        assert backend.available() is False
        with pytest.raises(CodecUnavailableError, match="'br'"):
            backend.create(4, 18)
        with pytest.raises(CodecUnavailableError):
            backend.compress_once(b"x", 4)

    @pytest.mark.parametrize("name", ["brotli", "brotlicffi"])
    def test_either_library_serves(self, monkeypatch, name: str) -> None:
        install(monkeypatch, **{name: fake_brotli(name)})
        assert BrotliBackend().available() is True
        assert brotli_library().__name__ == name

    def test_a_stream_decodes_after_each_flush(self, monkeypatch) -> None:
        install(monkeypatch, brotli=fake_brotli())
        _round_trip(BrotliBackend().create(5, 18), _inflate_reader())

    @pytest.mark.parametrize(("window", "expected"), [(18, 18), (None, 22)])
    def test_the_window_reaches_the_library(self, monkeypatch, window, expected):
        install(monkeypatch, brotli=fake_brotli())
        compressor = BrotliBackend().create(5, window)
        assert compressor._stream.lgwin == expected
        assert compressor._stream.quality == 5

    def test_compress_once(self, monkeypatch) -> None:
        install(monkeypatch, brotli=fake_brotli())
        assert inflate(BrotliBackend().compress_once(b"abc" * 100, 4)) == b"abc" * 100

    def test_a_stream_cannot_reset(self, monkeypatch) -> None:
        install(monkeypatch, brotli=fake_brotli())
        assert BrotliBackend.resets is False
        with pytest.raises(NotImplementedError, match="window"):
            BrotliBackend().create(4, 18).reset()


class TestZstdBackend:
    """`ZstdBackend` maps `compression.zstd` and `zstandard` to one contract."""

    def test_unavailable_without_a_library(self, monkeypatch) -> None:
        install(monkeypatch)
        backend = ZstdBackend()
        assert backend.available() is False
        with pytest.raises(CodecUnavailableError, match="'zstd'"):
            backend.create(3, 17)
        with pytest.raises(CodecUnavailableError):
            backend.compress_once(b"x", 3)

    def test_the_standard_library_wins(self, monkeypatch) -> None:
        install(
            monkeypatch, compression_zstd=fake_stdlib_zstd(), zstandard=fake_zstandard()
        )
        assert zstd_library().__name__ == "compression.zstd"

    @pytest.mark.parametrize(
        "modules",
        [{"compression_zstd": fake_stdlib_zstd()}, {"zstandard": fake_zstandard()}],
        ids=["stdlib", "zstandard"],
    )
    def test_a_stream_decodes_after_each_flush(self, monkeypatch, modules) -> None:
        install(monkeypatch, **modules)
        assert ZstdBackend().available() is True
        _round_trip(ZstdBackend().create(3, 17), _inflate_reader())

    def test_stdlib_options_carry_the_level_and_the_window(self, monkeypatch) -> None:
        install(monkeypatch, compression_zstd=fake_stdlib_zstd())
        windowed = ZstdBackend().create(3, 17)._stream
        assert windowed.options == {"compression_level": 3, "window_log": 17}
        assert ZstdBackend().create(5, None)._stream.options == {
            "compression_level": 5,
            "window_log": ZSTD_WINDOW_LIMIT,
        }
        capped = ZstdBackend().create(22, 30)._stream.options
        assert capped["window_log"] == ZSTD_WINDOW_LIMIT

    def test_zstandard_parameters_carry_the_level_and_the_window(
        self, monkeypatch
    ) -> None:
        install(monkeypatch, zstandard=fake_zstandard())
        windowed = ZstdBackend().create(3, 17)._context.params
        assert (windowed.level, windowed.window_log) == (3, 17)
        plain = ZstdBackend().create(6, None)._context.params
        assert (plain.level, plain.window_log) == (6, ZSTD_WINDOW_LIMIT)

    def test_stdlib_reset_ends_the_frame_and_reuses_the_compressor(
        self, monkeypatch
    ) -> None:
        install(monkeypatch, compression_zstd=fake_stdlib_zstd())
        compressor = ZstdBackend().create(3, 17)
        first = compressor.compress(b"one") + compressor.reset()
        second = compressor.compress(b"two") + compressor.reset()
        assert inflate(first) == b"one"
        assert inflate(second) == b"two"
        assert compressor._stream.frames == 2

    def test_zstandard_reset_opens_a_new_stream(self, monkeypatch) -> None:
        install(monkeypatch, zstandard=fake_zstandard())
        compressor = ZstdBackend().create(3, 17)
        first = compressor.compress(b"one") + compressor.reset()
        second = compressor.compress(b"two") + compressor.finish()
        assert (inflate(first), inflate(second)) == (b"one", b"two")
        assert compressor._context.streams == 2

    @pytest.mark.parametrize(
        "modules",
        [{"compression_zstd": fake_stdlib_zstd()}, {"zstandard": fake_zstandard()}],
        ids=["stdlib", "zstandard"],
    )
    def test_compress_once_reuses_one_context_per_level(
        self, monkeypatch, modules
    ) -> None:
        install(monkeypatch, **modules)
        backend = ZstdBackend()
        for _ in range(2):
            assert inflate(backend.compress_once(b"abc" * 100, 3)) == b"abc" * 100
        backend.compress_once(b"abc", 4)
        assert sorted(backend._contexts.held) == [3, 4]

    @pytest.mark.parametrize(
        ("modules", "window"),
        [
            (
                {"compression_zstd": fake_stdlib_zstd()},
                lambda c: c.options["window_log"],
            ),
            ({"zstandard": fake_zstandard()}, lambda c: c.params.window_log),
        ],
        ids=["stdlib", "zstandard"],
    )
    def test_compress_once_caps_the_window_at_any_level(
        self, monkeypatch, modules, window
    ) -> None:
        install(monkeypatch, **modules)
        library = zstd_library()
        made = []

        class Recording(library.ZstdCompressor):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                made.append(self)

        monkeypatch.setattr(library, "ZstdCompressor", Recording)
        assert inflate(ZstdBackend().compress_once(b"abc" * 100, 22)) == b"abc" * 100
        [context] = made
        assert window(context) == ZSTD_WINDOW_LIMIT

    def test_the_configurable_windows_stop_at_the_limit(self) -> None:
        assert ZstdBackend.windows == (10, ZSTD_WINDOW_LIMIT)


class TestDecoders:
    """Each codec decodes its own stream write by write, for tests and tools."""

    def test_gzip_decodes_each_flushed_write(self) -> None:
        compressor = GzipBackend().create(4, None)
        decode = GzipBackend().decoder()
        for part in PARTS:
            assert decode(compressor.compress(part) + compressor.flush()) == part
        assert decode(compressor.finish()) == b""

    @pytest.mark.parametrize("name", ["brotli", "brotlicffi"])
    def test_brotli_decodes_through_its_library(self, monkeypatch, name) -> None:
        install(monkeypatch, **{name: fake_brotli(name)})
        compressor = BrotliBackend().create(4, 18)
        decode = BrotliBackend().decoder()
        assert decode(compressor.compress(b"page") + compressor.flush()) == b"page"

    @pytest.mark.parametrize(
        "modules",
        [{"compression_zstd": fake_stdlib_zstd()}, {"zstandard": fake_zstandard()}],
        ids=["stdlib", "zstandard"],
    )
    def test_zstd_decodes_through_its_library(self, monkeypatch, modules) -> None:
        install(monkeypatch, **modules)
        compressor = ZstdBackend().create(3, 17)
        decode = ZstdBackend().decoder()
        assert decode(compressor.compress(b"page") + compressor.flush()) == b"page"

    def test_a_missing_library_has_no_decoder(self, monkeypatch) -> None:
        install(monkeypatch)
        with pytest.raises(CodecUnavailableError):
            ZstdBackend().decoder()
        with pytest.raises(CodecUnavailableError):
            BrotliBackend().decoder()

    def test_a_backend_without_a_decoder_says_so(self) -> None:
        class Plain(GzipBackend):
            coding = "plain"
            decoder = CompressionBackend.decoder

        with pytest.raises(NotImplementedError, match="no decoder for 'plain'"):
            Plain().decoder()


class TestRealLibraries:
    """The adapters drive the real codec libraries wherever they are installed."""

    def test_zstd(self) -> None:
        library = zstd_library()
        if library is None:
            pytest.skip("no zstd library is installed")
        compressor = ZstdBackend().create(3, 17)
        if library.__name__ == "compression.zstd":
            reader = library.ZstdDecompressor()
        else:
            reader = library.ZstdDecompressor().decompressobj()
        _round_trip(compressor, reader)
        once = ZstdBackend().compress_once(b"abc" * 1000, 3)
        assert library.ZstdDecompressor().decompress(once) == b"abc" * 1000

    def test_brotli(self) -> None:
        library = brotli_library()
        if library is None:
            pytest.skip("no brotli library is installed")

        class Reader:
            def __init__(self) -> None:
                self.inner = library.Decompressor()

            def decompress(self, data: bytes) -> bytes:
                return self.inner.process(data)

        _round_trip(BrotliBackend().create(4, 18), Reader())
        once = BrotliBackend().compress_once(b"abc" * 1000, 4)
        assert library.decompress(once) == b"abc" * 1000
