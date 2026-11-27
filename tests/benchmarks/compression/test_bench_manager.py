import asyncio
import gzip
from collections.abc import AsyncIterator, Iterator

import pytest
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from next.compression.manager import (
    compress_response,
    compression_manager,
    decode_response,
)


_ROW = '<p class="row">Row {n} of the static body, the text a page carries.</p>\n'
_SMALL = "".join(_ROW.format(n=n) for n in range(8)).encode()
_LARGE = "".join(_ROW.format(n=n) for n in range(270)).encode()
_CHUNK = "".join(_ROW.format(n=n) for n in range(4)).encode()
_ROUNDS = 200
_WARMUP_ROUNDS = 10
_CODINGS = pytest.mark.parametrize("coding", ["gzip", "br", "zstd"])
_CHUNKS = pytest.mark.parametrize("chunks", [10, 100])


def _request(coding: str) -> HttpRequest:
    return RequestFactory().get("/", headers={"Accept-Encoding": coding})


def _available(coding: str) -> None:
    """Skip a codec whose library this machine does not install."""
    if coding not in compression_manager.offered():
        pytest.skip(f"no codec library for {coding} here")


def _parts(count: int) -> Iterator[bytes]:
    for _ in range(count):
        yield _CHUNK


async def _aparts(count: int) -> AsyncIterator[bytes]:
    for _ in range(count):
        yield _CHUNK


async def _adrained(response: StreamingHttpResponse) -> bytes:
    return b"".join([part async for part in response.streaming_content])


class TestBenchResponse:
    """One buffered response through the compression the middleware runs."""

    @_CODINGS
    @pytest.mark.benchmark(group="compression.response")
    def test_large_body(self, coding: str, benchmark) -> None:
        """About 20 KB of HTML, compressed in one shot and marked encoded."""
        _available(coding)
        request = _request(coding)

        def setup() -> tuple[tuple[HttpRequest, HttpResponse], dict[str, object]]:
            return (request, HttpResponse(_LARGE)), {}

        response = benchmark.pedantic(
            compress_response, setup=setup, rounds=_ROUNDS, warmup_rounds=_WARMUP_ROUNDS
        )
        assert response["Content-Encoding"] == coding
        assert decode_response(response).startswith(_LARGE)

    @_CODINGS
    @pytest.mark.benchmark(group="compression.response")
    def test_small_body(self, coding: str, benchmark) -> None:
        """A body under `MIN_SIZE`, the floor every short response pays."""
        _available(coding)
        request = _request(coding)

        def setup() -> tuple[tuple[HttpRequest, HttpResponse], dict[str, object]]:
            return (request, HttpResponse(_SMALL)), {}

        response = benchmark.pedantic(
            compress_response, setup=setup, rounds=_ROUNDS, warmup_rounds=_WARMUP_ROUNDS
        )
        assert not response.has_header("Content-Encoding")
        assert response.content == _SMALL

    @pytest.mark.benchmark(group="compression.response")
    def test_large_body_cold(self, benchmark) -> None:
        """The first response after the settings reload, the backends built again."""
        request = _request("gzip")

        def setup() -> tuple[tuple[HttpRequest, HttpResponse], dict[str, object]]:
            compression_manager.reset()
            return (request, HttpResponse(_LARGE)), {}

        response = benchmark.pedantic(
            compress_response, setup=setup, rounds=_ROUNDS, warmup_rounds=_WARMUP_ROUNDS
        )
        assert gzip.decompress(response.content).startswith(_LARGE)


class TestBenchStream:
    """A streamed response encoded chunk by chunk, wrapped and read to its end."""

    @_CHUNKS
    @pytest.mark.benchmark(group="compression.stream")
    def test_async_coalescing(
        self, bench_loop: asyncio.AbstractEventLoop, chunks: int, benchmark
    ) -> None:
        """Writes within `COALESCE` of each other share one flush."""
        request = _request("gzip")

        def setup() -> tuple[tuple[StreamingHttpResponse], dict[str, object]]:
            response = StreamingHttpResponse(_aparts(chunks))
            return (compress_response(request, response),), {}

        def drain(response: StreamingHttpResponse) -> bytes:
            return bench_loop.run_until_complete(_adrained(response))

        body = benchmark.pedantic(
            drain, setup=setup, rounds=_ROUNDS, warmup_rounds=_WARMUP_ROUNDS
        )
        assert gzip.decompress(body) == _CHUNK * chunks

    @_CHUNKS
    @pytest.mark.benchmark(group="compression.stream")
    def test_sync_flush_per_chunk(self, chunks: int, benchmark) -> None:
        """Every chunk of a sync stream flushes as it arrives."""
        request = _request("gzip")

        def setup() -> tuple[tuple[StreamingHttpResponse], dict[str, object]]:
            response = StreamingHttpResponse(_parts(chunks))
            return (compress_response(request, response),), {}

        def drain(response: StreamingHttpResponse) -> bytes:
            return b"".join(response.streaming_content)

        body = benchmark.pedantic(
            drain, setup=setup, rounds=_ROUNDS, warmup_rounds=_WARMUP_ROUNDS
        )
        assert gzip.decompress(body) == _CHUNK * chunks
