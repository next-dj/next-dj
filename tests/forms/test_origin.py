from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

from django import forms as django_forms
from django.http import HttpRequest, QueryDict
from django.urls import clear_script_prefix, set_script_prefix

from next.forms import Form, origin
from next.forms.dispatch import _form_action_context_callable
from next.forms.origin import (
    _ORIGIN_MATCH_ATTR,
    _OriginMatch,
    _page_path_from_view,
    _resolve_origin,
    _url_kwargs_for_request,
)


SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"


def _origin_post(value: str) -> QueryDict:
    post = QueryDict(mutable=True)
    post["_next_form_origin"] = value
    return post


class TestResolveOrigin:
    """`_resolve_origin` turns the posted origin into a typed page identity."""

    def test_root_origin_resolves_to_page_source(self, mock_http_request) -> None:
        req = mock_http_request(method="POST", POST=_origin_post("/"))
        match = _resolve_origin(req)
        assert match is not None
        assert match.page_path == SITE_PAGES / "page.py"
        assert match.url_kwargs == {}
        assert match.origin == "/"

    def test_typed_kwargs_come_from_url_converters(self, mock_http_request) -> None:
        req = mock_http_request(method="POST", POST=_origin_post("/items/42/"))
        match = _resolve_origin(req)
        assert match is not None
        assert match.url_kwargs == {"id": 42}
        assert match.page_path == SITE_PAGES / "items" / "[int:id]" / "page.py"

    def test_query_string_is_kept_on_origin_but_not_resolved(
        self, mock_http_request
    ) -> None:
        req = mock_http_request(method="POST", POST=_origin_post("/items/7/?q=x"))
        match = _resolve_origin(req)
        assert match is not None
        assert match.origin == "/items/7/?q=x"
        assert match.url_kwargs == {"id": 7}

    def test_missing_post_attribute_yields_none(self) -> None:
        class NoPost:
            pass

        req = NoPost()
        assert _resolve_origin(req) is None

    def test_missing_origin_field_yields_none(self, mock_http_request) -> None:
        req = mock_http_request(method="POST", POST=QueryDict())
        assert _resolve_origin(req) is None

    def test_unresolvable_origin_yields_none(self, mock_http_request) -> None:
        req = mock_http_request(
            method="POST", POST=_origin_post("/no/such/route/anywhere/")
        )
        assert _resolve_origin(req) is None

    def test_result_is_memoised_on_the_request(
        self, mock_http_request, monkeypatch
    ) -> None:
        calls = {"n": 0}
        original = origin.resolve

        def counting_resolve(path, urlconf=None):
            calls["n"] += 1
            return original(path, urlconf=urlconf)

        monkeypatch.setattr(origin, "resolve", counting_resolve)
        req = mock_http_request(method="POST", POST=_origin_post("/"))
        first = _resolve_origin(req)
        second = _resolve_origin(req)
        assert first is second
        assert calls["n"] == 1
        assert getattr(req, _ORIGIN_MATCH_ATTR) is first

    def test_none_result_is_memoised_too(self, mock_http_request) -> None:
        req = mock_http_request(method="POST", POST=QueryDict())
        assert _resolve_origin(req) is None
        assert getattr(req, _ORIGIN_MATCH_ATTR) is None
        assert _resolve_origin(req) is None

    def test_script_prefix_is_stripped_before_resolve(self, mock_http_request) -> None:
        set_script_prefix("/app/")
        try:
            req = mock_http_request(method="POST", POST=_origin_post("/app/items/3/"))
            match = _resolve_origin(req)
        finally:
            clear_script_prefix()
        assert match is not None
        assert match.url_kwargs == {"id": 3}
        assert match.origin == "/app/items/3/"

    def test_per_request_urlconf_wins(self, mock_http_request) -> None:
        req = mock_http_request(
            method="POST",
            POST=_origin_post("/tenant/acme/"),
            urlconf="tests.forms.urls_tenant",
        )
        match = _resolve_origin(req)
        assert match is not None
        assert match.url_kwargs == {"slug": "acme"}
        assert match.page_path == Path("/tenant/pages/page.py")

    def test_i18n_prefixed_origin_resolves_under_active_language(
        self, mock_http_request
    ) -> None:
        req = mock_http_request(
            method="POST",
            POST=_origin_post("/en-us/docs/intro/"),
            urlconf="tests.forms.urls_i18n",
        )
        match = _resolve_origin(req)
        assert match is not None
        assert match.url_kwargs == {"slug": "intro"}
        assert match.page_path == Path("/i18n/pages/page.py")

    def test_i18n_origin_under_inactive_language_yields_none(
        self, mock_http_request
    ) -> None:
        req = mock_http_request(
            method="POST",
            POST=_origin_post("/fr/docs/intro/"),
            urlconf="tests.forms.urls_i18n",
        )
        assert _resolve_origin(req) is None

    def test_view_without_page_path_attribute_yields_none_path(
        self, mock_http_request
    ) -> None:
        req = mock_http_request(
            method="POST",
            POST=_origin_post("/bare/"),
            urlconf="tests.forms.urls_tenant",
        )
        match = _resolve_origin(req)
        assert match is not None
        assert match.page_path is None
        assert match.url_kwargs == {}


class TestPagePathFromView:
    """`_page_path_from_view` accepts Path or str attributes."""

    def test_path_attribute_passes_through(self) -> None:
        view = MagicMock()
        view.next_page_path = Path("/x/page.py")
        assert _page_path_from_view(view) == Path("/x/page.py")

    def test_str_attribute_becomes_path(self) -> None:
        view = MagicMock()
        view.next_page_path = "/y/page.py"
        assert _page_path_from_view(view) == Path("/y/page.py")

    def test_other_types_yield_none(self) -> None:
        view = MagicMock()
        view.next_page_path = 42
        assert _page_path_from_view(view) is None


class TestUrlKwargsForRequest:
    """`_url_kwargs_for_request` picks resolver kwargs or the origin match."""

    def test_dispatch_route_match_resolves_origin(self, mock_http_request) -> None:
        match = MagicMock()
        match.url_name = "form_action"
        match.kwargs = {"uid": "abcdef1234567890"}
        req = mock_http_request(
            method="POST", POST=_origin_post("/items/5/"), resolver_match=match
        )
        assert _url_kwargs_for_request(req) == {"id": 5}

    def test_dispatch_route_match_without_origin_yields_empty(
        self, mock_http_request
    ) -> None:
        match = MagicMock()
        match.url_name = "form_action"
        match.kwargs = {"uid": "abcdef1234567890"}
        req = mock_http_request(method="POST", POST=QueryDict(), resolver_match=match)
        assert _url_kwargs_for_request(req) == {}

    def test_page_resolver_kwargs_pass_through(self, mock_http_request) -> None:
        match = MagicMock()
        match.url_name = "items_int_id"
        match.kwargs = {"slug": "tea"}
        req = mock_http_request(method="GET", resolver_match=match)
        assert _url_kwargs_for_request(req) == {"slug": "tea"}

    def test_reserved_keys_are_filtered_from_resolver_kwargs(
        self, mock_http_request
    ) -> None:
        match = MagicMock()
        match.url_name = "page"
        match.kwargs = {"slug": "tea", "request": "spoof"}
        req = mock_http_request(method="GET", resolver_match=match)
        assert _url_kwargs_for_request(req) == {"slug": "tea"}

    def test_post_without_resolver_match_uses_origin(self, mock_http_request) -> None:
        req = mock_http_request(
            method="POST", POST=_origin_post("/items/9/"), resolver_match=None
        )
        assert _url_kwargs_for_request(req) == {"id": 9}

    def test_post_without_origin_yields_empty(self, mock_http_request) -> None:
        req = mock_http_request(method="POST", POST=QueryDict(), resolver_match=None)
        assert _url_kwargs_for_request(req) == {}

    def test_get_without_match_yields_empty(self, mock_http_request) -> None:
        req = mock_http_request(method="GET", resolver_match=None)
        assert _url_kwargs_for_request(req) == {}


class TestOriginMatchValue:
    """`_OriginMatch` is a plain frozen value object."""

    def test_fields_round_trip(self) -> None:
        match = _OriginMatch(
            page_path=Path("/p/page.py"), url_kwargs={"id": 1}, origin="/p/1/"
        )
        assert match.page_path == Path("/p/page.py")
        assert match.url_kwargs == {"id": 1}
        assert match.origin == "/p/1/"


class TestSiblingFormReRenderKwargs:
    """Sibling forms built on a dispatch POST see typed origin kwargs, not the uid."""

    def test_sibling_get_initial_receives_origin_kwargs(
        self, mock_http_request
    ) -> None:
        class SiblingInitialForm(Form):
            name = django_forms.CharField(max_length=10, required=False)

            seen: ClassVar[list] = []

            @classmethod
            def get_initial(cls, request: HttpRequest, **kwargs: object) -> dict:
                cls.seen.append(dict(kwargs))
                return {}

        match = MagicMock()
        match.url_name = "form_action"
        match.kwargs = {"uid": "abcdef1234567890"}
        req = mock_http_request(
            method="POST", POST=_origin_post("/items/5/"), resolver_match=match
        )
        _form_action_context_callable(SiblingInitialForm)(req)
        assert SiblingInitialForm.seen == [{"id": 5}]
