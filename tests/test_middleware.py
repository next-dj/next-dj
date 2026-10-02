import asyncio
import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from next.middleware import SharedCacheGuardMiddleware, guard_shared_cache
from next.testing import override_next_settings


def _response(cache_control: str | None, *, cookie: bool = True) -> HttpResponse:
    response = HttpResponse("x")
    if cache_control is not None:
        response["Cache-Control"] = cache_control
    response["CDN-Cache-Control"] = "max-age=600"
    response["Surrogate-Control"] = "max-age=600"
    if cookie:
        response.set_cookie("sessionid", "abc")
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
        ("cache_control", "cookie"),
        [("public, max-age=60", False), ("private, max-age=60", True), (None, True)],
        ids=["no_cookie", "private", "no_cache_control"],
    )
    def test_anything_else_passes_untouched(self, cache_control, cookie) -> None:
        response = _response(cache_control, cookie=cookie)
        guard_shared_cache(RequestFactory().get("/"), response)
        assert response.get("Cache-Control") == cache_control
        assert response["CDN-Cache-Control"] == "max-age=600"
        assert "Vary" not in response

    def test_each_path_is_reported_once(self, caplog) -> None:
        for path in ("/a/", "/a/", "/b/"):
            guard_shared_cache(RequestFactory().get(path), _response("public"))
        records = _guarded_records(caplog)
        assert [record.args for record in records] == [("/a/",), ("/b/",)]

    def test_a_reconfigure_reports_a_path_again(self, caplog) -> None:
        guard_shared_cache(RequestFactory().get("/a/"), _response("public"))
        with override_next_settings(CSP_NONCE=False):
            guard_shared_cache(RequestFactory().get("/a/"), _response("public"))
        assert len(_guarded_records(caplog)) == 2


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
