import asyncio
import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import ResolverMatch

from next.diagnostics import QUIET_PERIOD
from next.middleware import SharedCacheGuardMiddleware, guard_shared_cache
from next.testing import override_next_settings


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

    def test_each_path_is_reported_once(self, caplog) -> None:
        for path in ("/a/", "/a/", "/b/"):
            guard_shared_cache(RequestFactory().get(path), _response("public"))
        records = _guarded_records(caplog)
        assert [record.args for record in records] == [("/a/",), ("/b/",)]

    def test_each_route_is_reported_once_whatever_its_path(self, caplog) -> None:
        for path in ("/posts/1/", "/posts/2/"):
            request = RequestFactory().get(path)
            request.resolver_match = ResolverMatch(
                _response, (), {}, route="posts/<int:pk>/"
            )
            guard_shared_cache(request, _response("public"))
        records = _guarded_records(caplog)
        assert [record.args for record in records] == [("/posts/1/",)]

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
