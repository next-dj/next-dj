import types
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.test.utils import override_script_prefix
from django.urls import (
    URLPattern,
    URLResolver,
    clear_url_caches,
    include,
    path,
    re_path,
)
from django.urls.converters import REGISTERED_CONVERTERS, get_converters

from next.router.checks import check_router_scope, check_stranded_pages
from next.testing import override_next_settings


def plain(request: HttpRequest, **kwargs: object) -> HttpResponse:
    return HttpResponse(b"plain")


def _urlconf(count: int = 2) -> types.ModuleType:
    module = types.ModuleType("scope_check_urls")
    module.urlpatterns = [
        *(path(f"extra{n}/", plain) for n in range(count - 2)),
        path("accounts/login/", plain),
        path("api/", include([path("items/", plain), path("<int:pk>/", plain)])),
        path("<slug:any>/", plain),
        path("", include("next.urls")),
    ]
    return module


def _messages(count: int = 2, **router: object) -> list[str]:
    with (
        override_settings(ROOT_URLCONF=_urlconf(count)),
        override_next_settings(ROUTER={"SCOPE": "all", **router}),
    ):
        messages = check_router_scope()
    assert {message.id for message in messages} <= {"next.W191"}
    return [f"{message.msg} {message.hint}" for message in messages]


class TestRouterScope:
    """`next.W191` names the routes beyond pages the `all` scope fetches first."""

    @pytest.fixture(autouse=True)
    def _caches(self) -> Iterator[None]:
        yield
        clear_url_caches()

    def test_the_routes_beyond_pages_are_named_with_their_exclusion(self) -> None:
        (text,) = _messages()
        assert "under '/accounts/', '/api/'." in text
        assert "'EXCLUDE'] to ['/accounts/', '/api/']" in text

    def test_a_written_exclusion_keeps_its_routes_out(self) -> None:
        (text,) = _messages(EXCLUDE=["/api/"])
        assert "under '/accounts/'." in text
        assert "to ['/api/', '/accounts/']" in text

    def test_every_route_excluded_is_silent(self) -> None:
        assert _messages(EXCLUDE=["/api/", "/accounts/"]) == []

    def test_a_long_list_is_cut(self) -> None:
        (text,) = _messages(count=9)
        assert "and 4 more." in text

    def test_the_script_prefix_leads_each_route(self) -> None:
        with override_script_prefix("/app/"):
            (text,) = _messages()
        assert "'/app/accounts/'" in text

    @pytest.mark.parametrize(
        "router", [{"SCOPE": "pages"}, {"SCOPE": "opt_in"}, {"ENABLED": False}]
    )
    def test_another_scope_or_no_router_is_silent(self, router: dict) -> None:
        with (
            override_settings(ROOT_URLCONF=_urlconf()),
            override_next_settings(ROUTER=router),
        ):
            assert check_router_scope() == []


class _DigitYear:
    """A converter whose Unicode digit class the route table cannot carry."""

    regex = r"\d{4}"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: int) -> str:
        return str(value)


class _PageView:
    """A view the URLconf names directly, marked as a page as the file router marks one."""

    next_page_path = Path("x/page.py")

    def __call__(self, request: HttpRequest, **kwargs: object) -> HttpResponse:
        return HttpResponse(b"page")


page_view = _PageView()


def _stranded(*patterns: URLPattern | URLResolver, **router: object) -> list[str]:
    module = types.ModuleType("stranded_check_urls")
    module.urlpatterns = list(patterns)
    with override_settings(ROOT_URLCONF=module), override_next_settings(ROUTER=router):
        messages = check_stranded_pages()
    assert {message.id for message in messages} <= {"next.W192"}
    return [f"{message.msg} {message.hint}" for message in messages]


class TestStrandedPages:
    """`next.W192` names the pages the `pages` scope loads as a document, and why."""

    @pytest.fixture(autouse=True)
    def _caches(self) -> Iterator[None]:
        get_converters.cache_clear()
        with patch.dict(REGISTERED_CONVERTERS, {"dyear": _DigitYear()}):
            yield
        get_converters.cache_clear()
        clear_url_caches()

    def test_a_page_the_table_cannot_carry_is_named(self) -> None:
        (text,) = _stranded(path("year/<dyear:y>/", page_view))
        assert "'year/<dyear:y>/' (a converter or a regex" in text
        assert "data-next-router" in text

    def test_a_page_behind_a_wider_row_names_that_row(self) -> None:
        (text,) = _stranded(
            re_path(r"^blog/\d+/$", plain), path("blog/<slug:s>/", page_view)
        )
        assert "'blog/<slug:s>/' (behind '^blog/\\\\d+/$'" in text

    def test_a_page_in_an_include_the_table_cannot_read_is_named(self) -> None:
        (text,) = _stranded(path("f/<path:p>/", include([path("view/", page_view)])))
        assert "'f/<path:p>/view/'" in text

    def test_a_long_list_is_cut(self) -> None:
        (text,) = _stranded(*(path(f"y{n}/<dyear:y>/", page_view) for n in range(7)))
        assert "and 2 more" in text

    def test_carried_pages_are_silent(self) -> None:
        assert _stranded(path("blog/<slug:s>/", page_view), path("x/", plain)) == []

    @pytest.mark.parametrize(
        "router", [{"SCOPE": "all"}, {"SCOPE": "opt_in"}, {"ENABLED": False}]
    )
    def test_another_scope_or_no_router_is_silent(self, router: dict) -> None:
        assert _stranded(path("year/<dyear:y>/", page_view), **router) == []
