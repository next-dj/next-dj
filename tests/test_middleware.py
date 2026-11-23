import asyncio
import logging
import zlib
from inspect import iscoroutinefunction

import pytest
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory
from django.urls import ResolverMatch

from next.compression.backends import forget_codec_libraries
from next.compression.manager import compression_manager
from next.diagnostics import QUIET_PERIOD
from next.middleware import (
    CompressionMiddleware,
    SharedCacheGuardMiddleware,
    guard_shared_cache,
)
from next.testing import override_next_settings
from tests.support.compressors import install


def _response(
    cache_control: str | None, *, cookie: bool = True, cdn: bool = True
) -> HttpResponse:
    response = HttpResponse("x")
    if cache_control is not None:
        response["Cache-Control"] = cache_control
    if cdn:
        response["CDN-Cache-Control"] = "max-age=600"
        response["Surrogate-Control"] = "max-age=600"
    if cookie:
        response.set_cookie(
            "sessionid", "abc", secure=True, httponly=True, samesite="Lax"
        )
    return response


def _guarded_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "next.middleware"]


@pytest.fixture(autouse=True)
def _rearmed():
    with override_next_settings():
        yield


class TestGuardSharedCache:
    """A cookie under a shared `Cache-Control` sends the response private."""

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            ("public, max-age=60", "private, max-age=60"),
            (
                "s-maxage=300, stale-while-revalidate=30",
                "private, stale-while-revalidate=30",
            ),
            ("Public, S-MAXAGE=300, private", "private"),
        ],
        ids=["public", "s_maxage", "mixed_case"],
    )
    def test_a_shared_cache_with_a_cookie_goes_private(self, before, after) -> None:
        response = guard_shared_cache(RequestFactory().get("/a/"), _response(before))
        assert response["Cache-Control"] == after
        assert "CDN-Cache-Control" not in response
        assert "Surrogate-Control" not in response
        assert response["Vary"] == "Cookie"

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            ("private, max-age=60", "private, max-age=60"),
            (None, "private"),
            ("max-age=600", "private, max-age=600"),
        ],
        ids=["private_beside_a_cdn_header", "cdn_header_alone", "bare_max_age"],
    )
    def test_any_lifetime_a_shared_cache_reads_goes_private(
        self, before, after
    ) -> None:
        response = _response(before, cdn=before != "max-age=600")
        guard_shared_cache(RequestFactory().get("/"), response)
        assert response["Cache-Control"] == after
        assert "CDN-Cache-Control" not in response
        assert "Surrogate-Control" not in response

    def test_an_expires_header_counts_as_a_lifetime(self) -> None:
        response = _response(None, cdn=False)
        response["Expires"] = "Thu, 01 Jan 2099 00:00:00 GMT"
        guard_shared_cache(RequestFactory().get("/"), response)
        assert response["Cache-Control"] == "private"

    @pytest.mark.parametrize(
        ("cache_control", "cookie", "cdn"),
        [
            ("public, max-age=60", False, True),
            ("private, max-age=60", True, False),
            ("no-store, max-age=60", True, False),
            (None, True, False),
        ],
        ids=["no_cookie", "private", "no_store", "no_lifetime"],
    )
    def test_anything_else_passes_untouched(self, cache_control, cookie, cdn) -> None:
        response = _response(cache_control, cookie=cookie, cdn=cdn)
        guard_shared_cache(RequestFactory().get("/"), response)
        assert response.get("Cache-Control") == cache_control
        assert ("CDN-Cache-Control" in response) is cdn
        assert "Vary" not in response

    def test_unresolved_requests_share_one_key(self, caplog) -> None:
        """A path is request data, so it never becomes a key of its own."""
        for path in ("/a/", "/b/"):
            guard_shared_cache(RequestFactory().get(path), _response("public"))
        records = _guarded_records(caplog)
        assert [record.args for record in records] == [("/a/",)]

    def test_each_route_name_is_reported_once_whatever_its_path(self, caplog) -> None:
        for path, name in (("/posts/1/", "post"), ("/posts/2/", "post"), ("/", "home")):
            request = RequestFactory().get(path)
            request.resolver_match = ResolverMatch(_response, (), {}, url_name=name)
            guard_shared_cache(request, _response("public"))
        records = _guarded_records(caplog)
        assert [record.args for record in records] == [("/posts/1/",), ("/",)]

    def test_a_reconfigure_reports_a_path_again(self, caplog) -> None:
        guard_shared_cache(RequestFactory().get("/a/"), _response("public"))
        with override_next_settings(CSP_NONCE=False):
            guard_shared_cache(RequestFactory().get("/a/"), _response("public"))
        assert len(_guarded_records(caplog)) == 2

    def test_a_route_is_reported_again_after_the_quiet_period(
        self, caplog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr("next.diagnostics.monotonic", lambda: now[0])
        for _ in range(3):
            guard_shared_cache(RequestFactory().get("/a/"), _response("public"))
        now[0] += QUIET_PERIOD
        guard_shared_cache(RequestFactory().get("/a/"), _response("public"))
        records = _guarded_records(caplog)
        assert [record.suppressed for record in records] == [0, 2]


class TestSharedCacheGuardMiddleware:
    """The middleware runs the guard on sync and async stacks alike."""

    def test_a_sync_stack(self) -> None:
        middleware = SharedCacheGuardMiddleware(lambda _request: _response("public"))
        assert middleware(RequestFactory().get("/"))["Cache-Control"] == "private"

    def test_an_async_stack(self) -> None:
        async def view(request):
            return _response("public")

        middleware = SharedCacheGuardMiddleware(view)
        response = asyncio.run(middleware(RequestFactory().get("/")))
        assert response["Cache-Control"] == "private"


PAGE = "<html><body>" + "<p>row</p>" * 300 + "</body></html>"


@pytest.fixture()
def gzip_only(monkeypatch):
    """Refuse every optional codec library, so gzip is the one coding offered."""
    install(monkeypatch)
    forget_codec_libraries()
    compression_manager.reset()
    yield
    forget_codec_libraries()
    compression_manager.reset()


def _gzip_request(method: str = "GET"):
    return RequestFactory().generic(method, "/", HTTP_ACCEPT_ENCODING="gzip, br")


def _run(middleware: CompressionMiddleware):
    return asyncio.run(middleware(_gzip_request()))


@pytest.mark.usefixtures("gzip_only")
class TestCompressionMiddleware:
    """The middleware compresses on sync and async stacks, buffered and streamed."""

    def test_a_sync_stack_follows_the_handler(self) -> None:
        middleware = CompressionMiddleware(lambda _request: HttpResponse(PAGE))
        assert not iscoroutinefunction(middleware)
        response = middleware(_gzip_request())
        assert response["Content-Encoding"] == "gzip"
        assert response["Vary"] == "Accept-Encoding"
        assert zlib.decompress(response.content, 31).startswith(PAGE.encode())
        assert response["Content-Length"] == str(len(response.content))

    def test_an_async_stack_follows_the_handler(self) -> None:
        async def view(request):
            return HttpResponse(PAGE, headers={"ETag": '"v"'})

        middleware = CompressionMiddleware(view)
        assert iscoroutinefunction(middleware)
        response = _run(middleware)
        assert response["Content-Encoding"] == "gzip"
        assert response["ETag"] == 'W/"v"'

    def test_a_sync_stream_is_flushed_per_chunk(self) -> None:
        chunks = [b"<main>", b"<p>one</p>", b"<p>two</p></main>"]
        middleware = CompressionMiddleware(
            lambda _request: StreamingHttpResponse(iter(chunks))
        )
        response = middleware(_gzip_request())
        reader = zlib.decompressobj(31)
        decoded = [reader.decompress(part) for part in response]
        assert b"".join(decoded) == b"".join(chunks)
        assert decoded[: len(chunks)] == chunks
        assert "Content-Length" not in response

    def test_an_async_stream_is_flushed_per_chunk(self) -> None:
        chunks = [b"<main>", b"<p>one</p></main>"]

        async def source():
            for chunk in chunks:
                yield chunk

        async def view(request):
            return StreamingHttpResponse(source())

        async def read():
            response = await CompressionMiddleware(view)(_gzip_request())
            reader = zlib.decompressobj(31)
            return [reader.decompress(part) async for part in response]

        decoded = asyncio.run(read())
        assert decoded[: len(chunks)] == chunks

    def test_a_self_encoded_stream_passes_untouched(self) -> None:
        response = StreamingHttpResponse(iter([b"raw"]), content_type="text/html")
        response["Content-Encoding"] = "zstd"
        middleware = CompressionMiddleware(lambda _request: response)
        assert middleware(_gzip_request()) is response
        assert list(response) == [b"raw"]
        assert "Vary" not in response

    def test_no_transform_is_respected(self) -> None:
        middleware = CompressionMiddleware(
            lambda _request: HttpResponse(
                PAGE, headers={"Cache-Control": "no-transform"}
            )
        )
        assert "Content-Encoding" not in middleware(_gzip_request())

    def test_a_small_body_stays_plain(self) -> None:
        middleware = CompressionMiddleware(lambda _request: HttpResponse("<p>hi</p>"))
        response = middleware(_gzip_request())
        assert response.content == b"<p>hi</p>"
        assert "Content-Encoding" not in response
        assert "Vary" not in response

    def test_an_image_stays_plain(self) -> None:
        middleware = CompressionMiddleware(
            lambda _request: HttpResponse(b"x" * 5000, content_type="image/png")
        )
        response = middleware(_gzip_request())
        assert "Content-Encoding" not in response
        assert "Vary" not in response

    def test_disabled_is_a_pass_through(self) -> None:
        middleware = CompressionMiddleware(lambda _request: HttpResponse(PAGE))
        with override_next_settings(COMPRESSION={"ENABLED": False}):
            response = middleware(_gzip_request())
        assert response.content == PAGE.encode()
