import asyncio
import re
import zlib

import pytest

from next.compression import GzipBackend, StreamEncoding, padding
from next.compression.streams import media_type


_COMMENT = re.compile(rb"<!--[A-Za-z0-9._]*-->")
_SSE_COMMENT = re.compile(rb":[A-Za-z0-9._]*\n")


def _encoding(**options) -> StreamEncoding:
    return StreamEncoding("gzip", GzipBackend().create(4, None), **options)


class TestPadding:
    """`padding` answers a comment the content type ignores, of random length."""

    @pytest.mark.parametrize(
        ("content_type", "pattern", "frame"),
        [
            ("text/html; charset=utf-8", _COMMENT, 7),
            ("application/xhtml+xml", _COMMENT, 7),
            ("Text/Event-Stream", _SSE_COMMENT, 2),
        ],
    )
    def test_a_valid_comment_within_the_limit(self, content_type, pattern, frame):
        lengths = set()
        for _ in range(400):
            pad = padding(content_type, 8)
            assert pattern.fullmatch(pad)
            lengths.add(len(pad) - frame)
        assert lengths <= set(range(9))
        assert len(lengths) > 1

    def test_an_html_comment_never_closes_early(self) -> None:
        for _ in range(200):
            pad = padding("text/html", 32)
            assert pad.count(b"--") == 2
            assert b">" not in pad[:-1]

    @pytest.mark.parametrize("content_type", ["application/json", "text/css", ""])
    def test_other_types_get_nothing(self, content_type: str) -> None:
        assert padding(content_type, 32) == b""

    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_limit_of_zero_disables_it(self, limit: int) -> None:
        assert padding("text/html", limit) == b""

    def test_media_type_drops_parameters_and_case(self) -> None:
        assert media_type(" Text/HTML ; charset=utf-8") == "text/html"


class TestStreamEncoding:
    """A `StreamEncoding` writes each chunk padded, compressed and flushed."""

    def test_each_write_decodes_at_once(self) -> None:
        encoding = _encoding()
        reader = zlib.decompressobj(31)
        assert reader.decompress(encoding.write(b"<p>a</p>")) == b"<p>a</p>"
        assert reader.decompress(encoding.write(b"<p>b</p>")) == b"<p>b</p>"
        reader.decompress(encoding.end())
        assert reader.eof
        assert encoding.coding == "gzip"

    def test_an_empty_chunk_writes_nothing(self) -> None:
        assert _encoding().write(b"") == b""

    def test_a_write_carries_its_padding(self) -> None:
        encoding = _encoding(content_type="text/event-stream", pad=4)
        reader = zlib.decompressobj(31)
        out = reader.decompress(encoding.write(b"data: 1\n\n"))
        assert out.startswith(b"data: 1\n\n")
        assert _SSE_COMMENT.fullmatch(out[len(b"data: 1\n\n") :])

    def test_a_resetting_encoding_drops_the_window(self) -> None:
        event = bytes(range(256)) * 2
        resetting, shared = _encoding(reset=True), _encoding()
        first = resetting.write(event)
        again = resetting.write(event)
        shared.write(event)
        assert len(again) > 4 * len(shared.write(event))
        reader = zlib.decompressobj(31)
        assert reader.decompress(first + again) == event * 2

    def test_offloads_past_the_limit(self) -> None:
        encoding = _encoding(offload_bytes=4)
        assert encoding.offloads(b"12345") is True
        assert encoding.offloads(b"1234") is False

    @pytest.mark.parametrize(
        ("chunk", "offload", "away"),
        [(b"x" * 10, None, True), (b"x", None, False), (b"x", True, True)],
    )
    def test_awrite_compresses_off_the_loop_when_asked(
        self, monkeypatch, chunk, offload, away
    ):
        encoding = _encoding(offload_bytes=4)
        offloaded = []
        to_thread = asyncio.to_thread

        async def spy(func, *args):
            offloaded.append(args)
            return await to_thread(func, *args)

        monkeypatch.setattr("next.compression.streams.asyncio.to_thread", spy)

        async def run() -> bytes:
            return await encoding.awrite(chunk, offload=offload)

        out = asyncio.run(run())
        assert zlib.decompressobj(31).decompress(out) == chunk
        assert bool(offloaded) is away

    def test_wrap_skips_empty_output_and_closes_the_source(self) -> None:
        closed = []

        def source():
            try:
                yield b"one"
                yield b""
                yield b"two"
            finally:
                closed.append(True)

        parts = list(_encoding().wrap(source()))
        assert all(parts)
        assert zlib.decompress(b"".join(parts), 31) == b"onetwo"
        assert closed == [True]

    def test_wrap_closes_the_source_on_disconnect(self) -> None:
        closed = []

        def source():
            try:
                yield b"one"
                yield b"two"
            finally:
                closed.append(True)

        stream = _encoding().wrap(source())
        next(stream)
        stream.close()
        assert closed == [True]

    def test_wrap_accepts_a_source_without_close(self) -> None:
        body = b"".join(_encoding().wrap([b"a", b"b"]))
        assert zlib.decompress(body, 31) == b"ab"

    def test_awrap_encodes_and_closes_the_source(self) -> None:
        closed = []

        async def source():
            try:
                yield b"one"
                yield b""
                yield b"two"
            finally:
                closed.append(True)

        async def run() -> list[bytes]:
            return [part async for part in _encoding().awrap(source())]

        parts = asyncio.run(run())
        assert all(parts)
        assert zlib.decompress(b"".join(parts), 31) == b"onetwo"
        assert closed == [True]

    def test_awrap_accepts_a_source_without_aclose(self) -> None:
        class Source:
            def __init__(self) -> None:
                self.items = [b"a", b"b"]

            def __aiter__(self) -> "Source":
                return self

            async def __anext__(self) -> bytes:
                if not self.items:
                    raise StopAsyncIteration
                return self.items.pop(0)

        async def run() -> bytes:
            return b"".join([part async for part in _encoding().awrap(Source())])

        assert zlib.decompress(asyncio.run(run()), 31) == b"ab"

    def test_an_empty_end_is_not_yielded(self) -> None:
        class Silent:
            def compress(self, data: bytes) -> bytes:
                return data

            def flush(self) -> bytes:
                return b""

            def finish(self) -> bytes:
                return b""

        encoding = StreamEncoding("identity", Silent())

        async def run() -> list[bytes]:
            return [part async for part in encoding.awrap(_aiter([b"a"]))]

        assert list(encoding.wrap([b"a"])) == [b"a"]
        assert asyncio.run(run()) == [b"a"]


async def _aiter(items):
    for item in items:
        yield item
