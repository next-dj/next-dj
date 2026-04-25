from django.http import HttpRequest
from django.test import RequestFactory

from next.deps.cache import DependencyCache
from next.deps.context import ResolutionContext
from next.testing.deps import make_resolution_context, resolve_call


class TestMakeResolutionContext:
    """`make_resolution_context` fills sensible defaults."""

    def test_defaults_are_empty(self) -> None:
        ctx = make_resolution_context()
        assert isinstance(ctx, ResolutionContext)
        assert ctx.request is None
        assert ctx.form is None
        assert ctx.url_kwargs == {}
        assert ctx.context_data == {}
        assert isinstance(ctx.cache, DependencyCache)

    def test_forwards_fields(self) -> None:
        ctx = make_resolution_context(
            form="f",
            url_kwargs={"slug": "abc"},
            context_data={"title": "T"},
        )
        assert ctx.form == "f"
        assert ctx.url_kwargs == {"slug": "abc"}
        assert ctx.context_data == {"title": "T"}

    def test_fresh_cache_per_call(self) -> None:
        a = make_resolution_context()
        b = make_resolution_context()
        assert a.cache is not b.cache


class TestResolveCall:
    """`resolve_call` returns the kwargs that would be passed to `func`."""

    def test_resolves_http_request_by_type(self) -> None:
        req = RequestFactory().get("/x")

        def view(request: HttpRequest) -> None:
            return None

        kwargs = resolve_call(view, request=req)
        assert kwargs["request"] is req

    def test_resolves_by_name_from_context_data(self) -> None:
        def renderer(title: str = "") -> None:
            return None

        kwargs = resolve_call(renderer, context_data={"title": "Hello"})
        assert kwargs["title"] == "Hello"

    def test_returns_default_when_no_provider_matches(self) -> None:
        def fn(missing: str = "fallback") -> None:
            return None

        kwargs = resolve_call(fn)
        assert kwargs["missing"] == "fallback"
