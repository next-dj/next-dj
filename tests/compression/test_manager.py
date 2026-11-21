import asyncio
import inspect
import io
import logging
import os
import zlib

import django
import pytest
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.test import RequestFactory, override_settings

from next.compression import (
    OPT_OUT,
    CompressionBackend,
    CompressionBackendError,
    CompressionRegistry,
    GzipBackend,
    acompress_response,
    compress_response,
    compression_backend_loaded,
    compression_manager,
    decode_response,
    no_compress,
    offloads,
    prepare_stream,
)
from next.compression.manager import (
    GZIP_MIDDLEWARE,
    MIDDLEWARE,
    forget_compression,
    response_decoder,
)
from next.testing import capture_signals, override_next_settings
from tests.support.compressors import fake_brotli, fake_stdlib_zstd, inflate, install
from tests.support.races import LockWonByAnotherThread


BODY = "<html><body>" + "<p>row</p>" * 300 + "</body></html>"
GZIP_ONLY = ["next.compression.GzipBackend"]


def _request(accept: str = "gzip, br, zstd", method: str = "GET"):
    factory = RequestFactory()
    return factory.generic(method, "/", HTTP_ACCEPT_ENCODING=accept)


def _compression(**scope: object):
    return override_next_settings(COMPRESSION=scope)


def exclude_everything(request, response) -> bool:
    return True


def exclude_nothing(request, response) -> bool:
    return False


NOT_CALLABLE = 42


class LevelLess(CompressionBackend):
    coding = "custom"
    level = 2
    levels = (1, 9)
    window = None
    windows = None

    def available(self) -> bool:
        return True

    def create(self, level, window):
        self.created = (level, window)
        return GzipBackend().create(level, window)

    def compress_once(self, data, level):
        return GzipBackend().compress_once(data, level)


class TestRegistry:
    """`CompressionRegistry` keeps settings order, the first backend of a coding wins."""

    def test_first_coding_wins(self) -> None:
        registry = CompressionRegistry()
        first, second = GzipBackend(), GzipBackend()
        assert registry.register(first) is True
        assert registry.register(second) is False
        assert registry.get("gzip") is first
        assert registry.get("br") is None
        assert registry.codings() == ("gzip",)
        assert list(registry) == [first]
        assert len(registry) == 1
        assert registry.version == 1


class TestManager:
    """`CompressionManager` builds the backends on first use and offers by `ORDER`."""

    def test_nothing_builds_before_a_compressible_response(self, monkeypatch) -> None:
        install(monkeypatch)
        compress_response(_request(), HttpResponse(b"x", content_type="image/png"))
        assert compression_manager._state is None
        compress_response(_request(), HttpResponse(BODY))
        assert compression_manager._state is not None

    def test_order_filters_unavailable_and_unknown_codings(self, monkeypatch) -> None:
        install(monkeypatch, brotli=fake_brotli())
        with _compression(ORDER=["br", "zstd", "deflate", "gzip", "br"]):
            assert compression_manager.offered() == ("br", "gzip")
            assert compression_manager.registry.codings() == ("zstd", "br", "gzip")

    def test_settings_expose_the_scope(self) -> None:
        with _compression(MIN_SIZE=10):
            assert compression_manager.settings.min_size == 10

    def test_a_settings_change_rebuilds(self, monkeypatch) -> None:
        install(monkeypatch)
        assert compression_manager.offered() == ("gzip",)
        with _compression(ORDER=["zstd"]):
            assert compression_manager.offered() == ()
        assert compression_manager.offered() == ("gzip",)

    def test_a_thread_that_lost_the_race_keeps_the_backends_the_winner_built(
        self, monkeypatch
    ) -> None:
        compression_manager.offered()
        built = compression_manager._state
        lock = LockWonByAnotherThread(compression_manager, "_state", value=built)
        monkeypatch.setattr(compression_manager, "_lock", lock)
        monkeypatch.setattr(compression_manager, "_state", None)

        assert compression_manager.offered() == built.offered
        assert lock.entered == 1
        assert compression_manager._state is built

    def test_forget_drops_the_backends(self) -> None:
        compression_manager.offered()
        forget_compression(sender=None)
        assert compression_manager._state is None

    def test_each_loaded_backend_is_announced(self) -> None:
        with (
            capture_signals(compression_backend_loaded) as recorder,
            _compression(BACKENDS=GZIP_ONLY),
        ):
            compression_manager.offered()
        event = recorder.first_for(compression_backend_loaded)
        assert event.sender is GzipBackend
        assert event.kwargs["config"] == {"BACKEND": GZIP_ONLY[0]}
        assert isinstance(event.kwargs["instance"], GzipBackend)

    @pytest.mark.parametrize(
        ("dotted", "problem"),
        [
            ("next.compression.Missing", "does not import"),
            ("next.compression.manager.compress_response", "no CompressionBackend"),
            ("next.compression.CompressionBackend", "abstract class"),
        ],
    )
    def test_an_unusable_backend_is_logged_and_skipped(
        self, caplog, dotted: str, problem: str
    ) -> None:
        with (
            caplog.at_level(logging.WARNING, "next.compression.manager"),
            _compression(BACKENDS=[dotted, *GZIP_ONLY]),
        ):
            assert compression_manager.offered() == ("gzip",)
        assert problem in caplog.text
        assert "skips it" in caplog.text

    def test_an_unusable_backend_raises_under_debug(self) -> None:
        with (
            override_settings(DEBUG=True),
            _compression(BACKENDS=["next.compression.Missing"]),
            pytest.raises(CompressionBackendError, match="'BACKENDS'"),
        ):
            compression_manager.offered()

    @pytest.mark.parametrize(
        ("levels", "windows", "expected"),
        [({"br": 9}, {"br": 20}, (9, 20)), ({"br": 99}, {"br": 99}, (4, 18))],
    )
    def test_levels_and_windows_fall_back_out_of_range(
        self, monkeypatch, levels, windows, expected
    ) -> None:
        install(monkeypatch, brotli=fake_brotli())
        with _compression(LEVELS=levels, WINDOWS=windows):
            response = HttpResponse(BODY)
            response["Content-Type"] = "text/event-stream"
            encoding = prepare_stream(_request("br"), response)
        stream = encoding._compressor._stream
        assert (stream.quality, stream.lgwin) == expected

    def test_a_codec_without_levels_takes_its_own_default(self) -> None:
        dotted = f"{__name__}.LevelLess"
        with _compression(BACKENDS=[dotted], ORDER=["custom"]):
            response = StreamingHttpResponse([b"x"], content_type="text/html")
            encoding = prepare_stream(_request("custom"), response)
            backend = compression_manager.registry.get("custom")
        assert encoding.coding == "custom"
        assert backend.created == (2, None)


class TestEligibility:
    """`compress_response` encodes only what the client and the response allow."""

    def test_a_large_body_is_compressed_once(self, monkeypatch) -> None:
        install(monkeypatch)
        response = compress_response(_request(), HttpResponse(BODY))
        assert response["Content-Encoding"] == "gzip"
        assert response["Vary"] == "Accept-Encoding"
        body = zlib.decompress(response.content, 31)
        assert body.startswith(BODY.encode())
        assert response["Content-Length"] == str(len(response.content))

    def test_the_padding_follows_an_html_body(self, monkeypatch) -> None:
        install(monkeypatch)
        with _compression(PADDING=16):
            response = compress_response(_request(), HttpResponse(BODY))
        tail = zlib.decompress(response.content, 31)[len(BODY) :]
        assert tail.startswith(b"<!--")
        assert tail.endswith(b"-->")
        assert len(tail) <= 16 + 7

    @pytest.mark.parametrize(
        ("etag", "expected"), [('"v1"', 'W/"v1"'), ('W/"v1"', 'W/"v1"')]
    )
    def test_a_strong_etag_turns_weak(self, monkeypatch, etag, expected) -> None:
        install(monkeypatch)
        response = HttpResponse(BODY, headers={"ETag": etag})
        assert compress_response(_request(), response)["ETag"] == expected

    @pytest.mark.parametrize(
        "response",
        [
            HttpResponse(BODY, headers={"Content-Encoding": "br"}),
            HttpResponse(BODY, status=204),
            HttpResponse(BODY, status=206),
            HttpResponse(BODY, status=304),
            HttpResponse(BODY, status=101),
            HttpResponse(BODY, headers={"Content-Range": "bytes 0-9/100"}),
            HttpResponse(BODY, headers={"Cache-Control": "public, No-Transform"}),
            HttpResponse(BODY, content_type="image/png"),
        ],
        ids=["encoded", "204", "206", "304", "1xx", "range", "no_transform", "image"],
    )
    def test_a_response_that_cannot_vary_is_untouched(self, response) -> None:
        before = response.content
        compress_response(_request(), response)
        assert response.content == before
        assert "Vary" not in response
        assert response.get("Content-Encoding") in {None, "br"}

    def test_the_opt_out_attribute(self) -> None:
        response = HttpResponse(BODY)
        setattr(response, OPT_OUT, False)
        compress_response(_request(), response)
        assert "Content-Encoding" not in response

    def test_a_file_response_keeps_the_server_file_path(self) -> None:
        response = FileResponse(io.BytesIO(BODY.encode()), content_type="text/html")
        compress_response(_request(), response)
        assert response.file_to_stream is not None
        assert "Content-Encoding" not in response
        assert "Vary" not in response
        assert b"".join(response) == BODY.encode()

    def test_a_file_response_over_an_iterator_compresses_as_a_stream(
        self, monkeypatch
    ) -> None:
        install(monkeypatch)
        response = FileResponse(iter([BODY.encode()]), content_type="text/html")
        compress_response(_request(), response)
        assert response["Content-Encoding"] == "gzip"
        assert zlib.decompress(b"".join(response), 31) == BODY.encode()

    def test_no_transform_is_ignored_when_told(self, monkeypatch) -> None:
        install(monkeypatch)
        response = HttpResponse(BODY, headers={"Cache-Control": "no-transform"})
        with _compression(RESPECT_NO_TRANSFORM=False):
            compress_response(_request(), response)
        assert response["Content-Encoding"] == "gzip"

    def test_disabled_is_a_pass_through(self) -> None:
        response = HttpResponse(BODY)
        with _compression(ENABLED=False):
            compress_response(_request(), response)
        assert "Vary" not in response
        assert "Content-Encoding" not in response

    @pytest.mark.parametrize(
        ("content_types", "content_type", "encoded"),
        [
            (["text/*"], "text/css", True),
            (["application/json"], "application/json; charset=utf-8", True),
            (["application/json"], "text/html", False),
        ],
    )
    def test_content_types_filter(
        self, monkeypatch, content_types, content_type, encoded
    ) -> None:
        install(monkeypatch)
        response = HttpResponse(BODY, content_type=content_type)
        with _compression(CONTENT_TYPES=content_types):
            compress_response(_request(), response)
        assert ("Content-Encoding" in response) is encoded

    def test_a_body_under_min_size_still_varies(self) -> None:
        response = compress_response(_request(), HttpResponse("<p>short</p>"))
        assert "Content-Encoding" not in response
        assert response["Vary"] == "Accept-Encoding"

    def test_a_body_that_grows_is_kept(self, monkeypatch) -> None:
        install(monkeypatch)
        noise = os.urandom(2048)
        with _compression(MIN_SIZE=0, CONTENT_TYPES=["application/octet-stream"]):
            response = HttpResponse(noise, content_type="application/octet-stream")
            compress_response(_request(), response)
        assert response.content == noise
        assert "Content-Encoding" not in response

    def test_head_varies_without_a_body(self) -> None:
        response = compress_response(_request(method="HEAD"), HttpResponse(BODY))
        assert response["Vary"] == "Accept-Encoding"
        assert "Content-Encoding" not in response

    @pytest.mark.parametrize("accept", ["", "identity", "deflate", "gzip;q=0"])
    def test_no_acceptable_coding(self, accept: str) -> None:
        response = compress_response(_request(accept), HttpResponse(BODY))
        assert "Content-Encoding" not in response
        assert response["Vary"] == "Accept-Encoding"

    def test_exclude_keeps_a_response_plain_without_vary(self) -> None:
        with _compression(EXCLUDE=f"{__name__}.exclude_everything"):
            response = compress_response(_request(), HttpResponse(BODY))
        assert "Content-Encoding" not in response
        assert "Vary" not in response

    def test_exclude_answering_false_compresses(self, monkeypatch) -> None:
        install(monkeypatch)
        with _compression(EXCLUDE=f"{__name__}.exclude_nothing"):
            response = compress_response(_request(), HttpResponse(BODY))
        assert response["Content-Encoding"] == "gzip"

    @pytest.mark.parametrize(
        ("dotted", "problem"),
        [
            (f"{__name__}.missing", "does not import"),
            (f"{__name__}.NOT_CALLABLE", "no callable"),
        ],
    )
    def test_an_unusable_exclude_is_skipped(
        self, monkeypatch, caplog, dotted, problem
    ) -> None:
        install(monkeypatch)
        with caplog.at_level(logging.WARNING), _compression(EXCLUDE=dotted):
            response = compress_response(_request(), HttpResponse(BODY))
        assert response["Content-Encoding"] == "gzip"
        assert problem in caplog.text

    def test_a_response_of_its_own_kind_is_left_alone(self) -> None:
        response = HttpResponseBase(content_type="text/html")
        assert compress_response(_request(), response) is response
        assert response["Vary"] == "Accept-Encoding"

    def test_the_chosen_coding_follows_the_client(self, monkeypatch) -> None:
        install(monkeypatch, compression_zstd=fake_stdlib_zstd(), brotli=fake_brotli())
        assert (
            compress_response(_request("br, gzip"), HttpResponse(BODY))[
                "Content-Encoding"
            ]
            == "br"
        )
        response = compress_response(_request("zstd"), HttpResponse(BODY))
        assert response["Content-Encoding"] == "zstd"
        assert inflate(response.content).startswith(BODY.encode())


class TestStreams:
    """A stream without its own coding is wrapped and flushed per chunk."""

    def test_a_sync_stream(self, monkeypatch) -> None:
        install(monkeypatch)
        response = StreamingHttpResponse(
            iter([b"<p>a</p>", b"<p>b</p>"]), headers={"Content-Length": "16"}
        )
        compress_response(_request(), response)
        assert response["Content-Encoding"] == "gzip"
        assert "Content-Length" not in response
        assert zlib.decompress(b"".join(response), 31) == b"<p>a</p><p>b</p>"

    def test_an_async_stream(self, monkeypatch) -> None:
        install(monkeypatch)

        async def source():
            yield b"<p>a</p>"
            yield b"<p>b</p>"

        response = StreamingHttpResponse(source())
        compress_response(_request(), response)

        async def read() -> bytes:
            return b"".join([part async for part in response])

        assert response.is_async
        assert zlib.decompress(asyncio.run(read()), 31) == b"<p>a</p><p>b</p>"

    @pytest.mark.parametrize(("sse", "encoded"), [(True, True), (False, False)])
    def test_an_event_stream_follows_sse(self, monkeypatch, sse, encoded) -> None:
        install(monkeypatch)
        response = StreamingHttpResponse(
            iter([b"data: 1\n\n"]), content_type="text/event-stream"
        )
        with _compression(SSE=sse):
            compress_response(_request(), response)
        assert ("Content-Encoding" in response) is encoded


class TestAsyncBuffered:
    """`acompress_response` compresses a large body in a worker thread."""

    @pytest.mark.parametrize(("limit", "away"), [(16, True), (10**9, False)])
    def test_the_offload_rule(self, monkeypatch, limit, away) -> None:
        install(monkeypatch)
        offloaded = []
        to_thread = asyncio.to_thread

        async def spy(func, *args):
            offloaded.append(func)
            return await to_thread(func, *args)

        monkeypatch.setattr("next.compression.manager.asyncio.to_thread", spy)
        with _compression(OFFLOAD_BYTES=limit):
            response = asyncio.run(acompress_response(_request(), HttpResponse(BODY)))
            assert offloads(b"x" * 17) is away
        assert response["Content-Encoding"] == "gzip"
        assert bool(offloaded) is away

    def test_a_plain_response_passes(self) -> None:
        response = HttpResponse(b"x", content_type="image/png")
        assert asyncio.run(acompress_response(_request(), response)) is response


class TestPrepareStream:
    """`prepare_stream` negotiates for a stream that compresses itself."""

    def test_a_document_stream_takes_the_coding(self, monkeypatch) -> None:
        install(monkeypatch)
        response = StreamingHttpResponse(
            [b""], content_type="text/html", headers={"ETag": '"a"'}
        )
        response["Content-Length"] = "10"
        encoding = prepare_stream(_request(), response)
        assert encoding.coding == "gzip"
        assert response["Content-Encoding"] == "gzip"
        assert response["Vary"] == "Accept-Encoding"
        assert response["ETag"] == 'W/"a"'
        assert "Content-Length" not in response
        out = zlib.decompressobj(31).decompress(encoding.write(b"<main>"))
        assert out.startswith(b"<main><!--")
        assert compress_response(_request(), response) is response

    def test_streams_owned_by_the_middleware(self) -> None:
        response = StreamingHttpResponse([b""], content_type="text/html")
        with _compression(STREAMS="middleware"):
            assert prepare_stream(_request(), response) is None
        assert "Vary" not in response
        assert getattr(response, OPT_OUT, True) is True

    def test_a_plain_answer_keeps_the_middleware_away(self) -> None:
        response = StreamingHttpResponse([b""], content_type="text/html")
        assert prepare_stream(_request("identity"), response) is None
        assert getattr(response, OPT_OUT) is False

    @pytest.mark.parametrize(
        ("mode", "sse", "coding"),
        [("off", True, None), ("shared", False, "gzip"), ("reset", True, "gzip")],
    )
    def test_a_topic_follows_sse_topics(self, monkeypatch, mode, sse, coding) -> None:
        install(monkeypatch)
        response = StreamingHttpResponse([b""], content_type="text/event-stream")
        with _compression(SSE_TOPICS=mode, SSE=sse):
            encoding = prepare_stream(_request(), response, topic=True)
        assert (encoding and encoding.coding) == coding
        if mode == "off":
            assert "Vary" not in response

    def test_reset_never_picks_brotli(self, monkeypatch) -> None:
        install(monkeypatch, brotli=fake_brotli())
        response = StreamingHttpResponse([b""], content_type="text/event-stream")
        with _compression(SSE_TOPICS="reset", ORDER=["br"]):
            assert prepare_stream(_request("br"), response, topic=True) is None

    def test_a_reset_encoding_drops_the_window_per_write(self, monkeypatch) -> None:
        install(monkeypatch)
        response = StreamingHttpResponse([b""], content_type="text/event-stream")
        with _compression(SSE_TOPICS="reset", PADDING=0):
            encoding = prepare_stream(_request(), response, topic=True)
        event = bytes(range(256))
        first, again = encoding.write(event), encoding.write(event)
        assert len(again) > len(event)
        assert zlib.decompressobj(31).decompress(first + again) == event * 2

    def test_partial_event_streams_follow_sse(self) -> None:
        response = StreamingHttpResponse([b""], content_type="text/event-stream")
        with _compression(SSE=False):
            assert prepare_stream(_request(), response) is None


class TestDocumentCoding:
    """The coding a document stream goes out in, read before the response exists."""

    def test_a_stream_that_encodes_itself_buffers_nowhere(self, monkeypatch) -> None:
        install(monkeypatch)
        with override_settings(MIDDLEWARE=[GZIP_MIDDLEWARE]):
            coding = compression_manager.document_coding(_request())
        assert coding == ("gzip", False)

    def test_identity_answers_nothing(self) -> None:
        assert compression_manager.document_coding(_request("identity")) == (
            None,
            False,
        )

    @pytest.mark.parametrize(
        "scope", [{"ENABLED": False}, {"STREAMS": "middleware"}, {"ORDER": ["zstd"]}]
    )
    def test_django_gzip_buffers_below_six_one(self, monkeypatch, scope) -> None:
        install(monkeypatch)
        with _compression(**scope), override_settings(MIDDLEWARE=[GZIP_MIDDLEWARE]):
            coding = compression_manager.document_coding(_request("gzip"))
        assert coding == ("gzip", django.VERSION[:2] < (6, 1))

    def test_the_middleware_owning_streams_flushes_them(self, monkeypatch) -> None:
        install(monkeypatch)
        with (
            _compression(STREAMS="middleware"),
            override_settings(MIDDLEWARE=[MIDDLEWARE]),
        ):
            coding = compression_manager.document_coding(_request())
        assert coding == ("gzip", False)

    def test_html_outside_content_types_is_left_to_django(self, monkeypatch) -> None:
        install(monkeypatch)
        with _compression(CONTENT_TYPES=["application/json"]):
            assert compression_manager.document_coding(_request()) == (None, False)


class TestDecodeResponse:
    """`decode_response` reads a body back from whatever coding it was sent in."""

    def test_a_plain_body_reads_as_itself(self) -> None:
        response = HttpResponse(BODY)
        assert decode_response(response) == BODY.encode()
        response["Content-Encoding"] = "identity"
        assert decode_response(response) == BODY.encode()

    def test_a_buffered_body_decodes(self, monkeypatch) -> None:
        install(monkeypatch)
        response = compress_response(_request(), HttpResponse(BODY))
        assert decode_response(response).startswith(BODY.encode())

    def test_a_stream_decodes_write_by_write(self, monkeypatch) -> None:
        install(monkeypatch, brotli=fake_brotli())
        stream = StreamingHttpResponse(iter([b"<p>a</p>", b"<p>b</p>"]))
        compress_response(_request("br"), stream)
        assert stream["Content-Encoding"] == "br"
        decode = response_decoder(stream)
        assert [decode(part) for part in stream] == [b"<p>a</p>", b"<p>b</p>", b""]

    def test_an_unknown_coding_is_refused(self) -> None:
        response = HttpResponse(b"x", headers={"Content-Encoding": "deflate"})
        with pytest.raises(ValueError, match="'deflate'"):
            decode_response(response)


class TestNoCompress:
    """`no_compress` keeps every response of a plain view uncompressed."""

    def test_a_sync_view_is_marked(self) -> None:
        @no_compress
        def view(request):
            return HttpResponse(BODY)

        response = compress_response(_request(), view(_request()))
        assert getattr(response, OPT_OUT) is False
        assert "Content-Encoding" not in response
        assert view.__name__ == "view"

    def test_an_async_view_stays_async_and_is_marked(self) -> None:
        @no_compress
        async def view(request):
            return HttpResponse(BODY)

        assert inspect.iscoroutinefunction(view)
        response = asyncio.run(view(_request()))
        assert getattr(response, OPT_OUT) is False
