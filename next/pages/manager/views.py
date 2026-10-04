"""Page view factories and the `URLPattern` the file router mounts them under."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, cast

from django.http import Http404, HttpResponse
from django.urls import URLPattern, path

from next.caches import PageCache
from next.conf import fail_loudly, next_framework_settings
from next.deps.cache import render_dep_cache
from next.diagnostics import watch_degraded
from next.pages.loaders import (
    build_registered_loaders,
    has_load_errors,
    last_load_error,
    load_page_module,
)
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
    """A page view carrying the `page.py` path form dispatch maps an origin URL to."""

    next_page_path: Path

    def __call__(self, request: HttpRequest, **kwargs) -> HttpResponseBase: ...


def unified_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool = False,
) -> Callable[..., HttpResponseBase]:
    """Return the static or the resolving view of a page, as its body source requires.

    The view carries `next_page_path`, so form dispatch maps an origin URL back to it.
    """
    has_render = module is not None and callable(getattr(module, "render", None))
    view = (
        _resolving_view(page, file_path, module, broken_at_build=broken_at_build)
        if has_render or broken_at_build
        else _static_view(page, file_path)
    )
    cast("_RoutedPageView", view).next_page_path = file_path
    return view


def _raise_load_error(file_path: Path) -> None:
    """Raise the import failure of `file_path` when the framework fails loudly."""
    # `has_load_errors` runs first, so a site without a load error runs no stat.
    if has_load_errors() and fail_loudly():
        error = last_load_error(file_path)
        if error is not None:
            raise error


def _reloaded_module(file_path: Path) -> types.ModuleType | None:
    """Return the module now at `file_path`, raising its import failure or a 404."""
    # The memo re-reads by mtime and drops the error once the file imports.
    module, error = load_page_module(file_path)
    if error is not None:
        if fail_loudly():
            raise error
        raise Http404
    return module


def _static_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of a page whose body comes from files on disk.

    The body does not depend on the request, so the view renders the compiled
    composed template and resolves no body per request.
    """

    def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
        watch_degraded()
        _raise_load_error(file_path)
        shaper = partial_shaper_slot.get()
        intent = shaper.intent(request)
        if intent.zones:
            zone = shaper.zone_response(
                file_path, request, intent, dynamic_body=False, url_kwargs=dict(kwargs)
            )
            return finish_zone_response(zone, zone_policy(page, file_path), request)
        dep_cache = render_dep_cache(request)
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

    `broken_at_build` re-reads the module per request, so a fix takes effect without a
    restart and a still-broken file never bypasses the guards `render()` applies.
    """

    def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
        watch_degraded()
        if broken_at_build:
            active_module = _reloaded_module(file_path)
        else:
            _raise_load_error(file_path)
            active_module = module
        dep_cache = render_dep_cache(request)
        shaper = partial_shaper_slot.get()
        intent = shaper.intent(request)
        if intent.zones:
            policy = zone_policy(page, file_path)
        else:
            policy = response_policy(
                page, file_path, request, url_kwargs=kwargs, dep_cache=dep_cache
            )
            prepare_page_render(policy, request)
        resolution = page._resolve_page_body(
            file_path, active_module, request, _dep_cache=dep_cache, **kwargs
        )
        if resolution.http_response is not None:
            if intent.zones:
                return finish_zone_response(resolution.http_response, policy, request)
            return _finish_render_response(
                resolution.http_response, policy, request, file_path, shaper
            )
        if intent.zones:
            zone = shaper.zone_response(
                file_path,
                request,
                intent,
                dynamic_body=resolution.dynamic,
                url_kwargs=dict(kwargs),
            )
            return finish_zone_response(zone, policy, request)
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

    A browser may serve a stored full page in place of a zone fetch to the same URL,
    so a successful response that carries `Cache-Control` gets the partial `Vary`.
    """
    finish_response(response, policy, request, file_path)
    if (
        isinstance(response, HttpResponse)
        and response.status_code in _SUCCESS
        and response.has_header("Cache-Control")
    ):
        shaper.set_vary(response)
    return response


def _has_body_source(
    page: Page, file_path: Path, module: types.ModuleType | None
) -> bool:
    """Return True when `file_path` can produce a body or layout body."""
    if module is not None:
        if callable(getattr(module, "render", None)):
            return True
        if isinstance(getattr(module, "template", None), str):
            return True
    if any(loader.can_load(file_path) for loader in build_registered_loaders()):
        return True
    return page._layout_loader.can_load(file_path)


def _page_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase] | None:
    """Return the view of a real or virtual page, or `None` when it has no body.

    A `page.py` whose import failed still gets a view to surface that error per
    request, while a path that never existed is a template-only page with no module.
    """
    if not file_path.exists():
        if not _has_body_source(page, file_path, module=None):
            return None
        return unified_view(page, file_path, None)
    module, error = load_page_module(file_path)
    if module is None:
        if error is None:
            return None
        return unified_view(page, file_path, None, broken_at_build=True)
    if not _has_body_source(page, file_path, module):
        return None
    return unified_view(page, file_path, module)


def create_url_pattern(
    page: Page, url_path: str, file_path: Path, url_parser: URLPatternParser
) -> URLPattern | None:
    """Return a `path()` pattern for a page, template, or virtual entry."""
    # The error class comes through the parser, because importing next.urls
    # at module level would create an import cycle between next.pages and next.urls.
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

    The URL build calls it once with the router patterns it built. A router reuses its
    patterns between builds, so the count is read from the views they hold.
    """
    pages = {
        page_path
        for pattern in patterns
        if isinstance(pattern, URLPattern)
        and (page_path := getattr(pattern.callback, "next_page_path", None)) is not None
    }
    PageCache.fit(len(pages))


__all__ = ["create_url_pattern", "fit_page_caches", "unified_view"]
