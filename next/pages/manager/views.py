"""Page view factories and the `URLPattern` the file router mounts them under."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, cast

from django.http import Http404, HttpResponse
from django.urls import URLPattern, path

from next.caches import PageCache
from next.conf import next_framework_settings
from next.deps.cache import render_dep_cache
from next.diagnostics import watch_degraded
from next.pages.access import page_access, refusal, routed
from next.pages.loaders import load_page_module, load_python_module_memo, serves
from next.pages.responses import (
    finish_response,
    finish_zone_response,
    prepare_page_render,
    response_policy,
    zone_policy,
)
from next.ports import partial_shaper_slot


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase

    from next.pages.responses import ResponsePolicy
    from next.ports import PartialShaper
    from next.urls import URLPatternParser

    from . import Page


_SUCCESS: Final = range(200, 300)


class _RoutedPageView(Protocol):
    """A page view carrying the `page.py` path form dispatch maps an origin URL to.

    `login_required` is the attribute `LoginRequiredMiddleware` reads off a view.
    """

    next_page_path: Path
    login_required: bool

    def __call__(self, request: HttpRequest, **kwargs) -> HttpResponseBase: ...


def unified_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool = False,
) -> Callable[..., HttpResponseBase]:
    """Return the static or the resolving view of a page, as its body source requires.

    Dispatch maps an origin by `next_page_path`, and a public page skips the middleware.
    """
    has_render = module is not None and callable(getattr(module, "render", None))
    view = (
        _resolving_view(page, file_path, module, broken_at_build=broken_at_build)
        if has_render or broken_at_build
        else _static_view(page, file_path)
    )
    tagged = cast("_RoutedPageView", view)
    tagged.next_page_path = file_path
    tagged.login_required = not page_access(file_path).rule.public
    return view


def _static_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of a page whose body comes from files on disk.

    The body is request-independent, so the view renders the compiled template.
    """

    def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
        watch_degraded()
        dep_cache = render_dep_cache(request)
        denial = refusal(file_path, request, kwargs, dep_cache)
        if denial is not None:
            return denial
        shaper = partial_shaper_slot.get()
        intent = shaper.intent(request)
        if intent.zones:
            zone = shaper.zone_response(
                file_path,
                request,
                intent,
                dynamic_body=False,
                url_kwargs=dict(kwargs),
                dep_cache=dep_cache,
            )
            return finish_zone_response(zone, zone_policy(page, file_path), request)
        policy = response_policy(
            page, file_path, request, url_kwargs=kwargs, dep_cache=dep_cache
        )
        prepare_page_render(policy, request)
        response = HttpResponse(
            page.render(file_path, request, _dep_cache=dep_cache, **kwargs)
        )
        finish_response(response, policy, request, file_path)
        shaper.set_vary(response)
        return response

    return view


def _resolving_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool,
) -> Callable[..., HttpResponseBase]:
    """Return the view that resolves a per-request body before composing.

    `broken_at_build` re-reads the module per request, a 404 once it has no body.
    """

    def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
        watch_degraded()
        if broken_at_build and not routed(file_path):
            raise Http404
        dep_cache = render_dep_cache(request)
        denial = refusal(file_path, request, kwargs, dep_cache)
        if denial is not None:
            return denial
        active_module = (
            load_python_module_memo(file_path) if broken_at_build else module
        )
        shaper = partial_shaper_slot.get()
        intent = shaper.intent(request)
        resolution = page._resolve_page_body(
            file_path, active_module, request, _dep_cache=dep_cache, **kwargs
        )
        built = resolution.http_response
        if intent.zones:
            zone = built
            if zone is None:
                zone = shaper.zone_response(
                    file_path,
                    request,
                    intent,
                    dynamic_body=resolution.dynamic,
                    url_kwargs=dict(kwargs),
                    dep_cache=dep_cache,
                )
            return finish_zone_response(zone, zone_policy(page, file_path), request)
        policy = response_policy(
            page,
            file_path,
            request,
            url_kwargs=kwargs,
            dep_cache=dep_cache,
            response=built,
        )
        prepare_page_render(policy, request)
        if built is not None:
            return _finish_render_response(built, policy, request, file_path, shaper)
        body = resolution.body if resolution.body is not None else ""
        response = HttpResponse(
            page._render_composed(
                file_path, body, request, _dep_cache=dep_cache, **kwargs
            )
        )
        finish_response(response, policy, request, file_path)
        shaper.set_vary(response)
        return response

    return view


def _finish_render_response(
    response: HttpResponseBase,
    policy: ResponsePolicy,
    request: HttpRequest,
    file_path: Path,
    shaper: PartialShaper,
) -> HttpResponseBase:
    """Finish the full-page response `render()` returned, varying a cacheable one.

    A browser may serve a stored full page for a zone fetch, so `Vary` is added.
    """
    finish_response(response, policy, request, file_path)
    if (
        isinstance(response, HttpResponse)
        and response.status_code in _SUCCESS
        and response.has_header("Cache-Control")
    ):
        shaper.set_vary(response)
    return response


def _page_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase] | None:
    """Return the view of a real or virtual page, or `None` when it has no body.

    A `page.py` whose import failed keeps a view, so each request surfaces the error.
    """
    module, error = load_page_module(file_path)
    if not serves(file_path, module, error):
        return None
    return unified_view(page, file_path, module, broken_at_build=error is not None)


def create_url_pattern(
    page: Page, url_path: str, file_path: Path, url_parser: URLPatternParser
) -> URLPattern | None:
    """Return a `path()` pattern for a page, template, or virtual entry."""
    # The error class comes through the parser, as importing next.urls is a cycle.
    try:
        django_pattern, _parameters = url_parser.parse_url_pattern(url_path)
    except url_parser.parameter_error as exc:
        raise exc.with_file(file_path) from exc

    view = _page_view(page, file_path)
    if view is None:
        return None
    clean_name = url_parser.prepare_url_name(url_path)
    return path(
        django_pattern,
        view,
        name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
    )


def fit_page_caches(patterns: Iterable[object]) -> None:
    """Size the page-keyed memos by the pages that `patterns` mount.

    The URL build calls it once, and routers reuse patterns, so views give the count.
    """
    pages = {
        page_path
        for pattern in patterns
        if isinstance(pattern, URLPattern)
        and (page_path := getattr(pattern.callback, "next_page_path", None)) is not None
    }
    PageCache.fit(len(pages))


__all__ = ["create_url_pattern", "fit_page_caches", "unified_view"]
