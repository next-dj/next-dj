"""Page view factories and the `URLPattern` the file router mounts them under."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from django.http import Http404, HttpResponse
from django.urls import path

from next.conf import fail_loudly, next_framework_settings
from next.pages.loaders import (
    _load_python_module_memo,
    build_registered_loaders,
    has_load_errors,
    last_load_error,
)
from next.ports import partial_shaper_slot


if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.urls import URLPattern

    from next.urls import URLPatternParser

    from . import Page


class _RoutedPageView(Protocol):
    """A page view carrying the source path form dispatch resolves back to."""

    next_page_path: Path

    def __call__(self, request: HttpRequest, **kwargs) -> HttpResponseBase: ...


def unified_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool = False,
) -> Callable[..., HttpResponseBase]:
    """Return the view for a page, on the branch its body source dictates.

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


def _static_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of a page whose body comes from files on disk.

    Nothing about that body depends on the request, so the view serves
    the compiled composed template and resolves no body of its own.
    """

    def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
        # `has_load_errors` first, so a healthy site pays no stat.
        if has_load_errors() and fail_loudly():
            error = last_load_error(file_path)
            if error is not None:
                raise error
        shaper = partial_shaper_slot.get()
        intent = shaper.intent(request)
        if intent.zones:
            return shaper.zone_response(
                file_path, request, intent, dynamic_body=False, url_kwargs=dict(kwargs)
            )
        return HttpResponse(page.render(file_path, request, **kwargs))

    return view


def _resolving_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool,
) -> Callable[..., HttpResponseBase]:
    """Return the view that resolves a per-request body before composing.

    `broken_at_build` re-reads the module per request, so a fix lands without a
    restart and a still-broken file never skips the guards `render()` would apply.
    """

    def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
        active_module = module
        if broken_at_build:
            # The memo re-reads by mtime and drops the error once the file imports.
            active_module = _load_python_module_memo(file_path)
            error = last_load_error(file_path)
            if error is not None:
                if fail_loudly():
                    raise error
                raise Http404
        # Both guards first, so a healthy site pays no stat.
        elif has_load_errors() and fail_loudly():
            error = last_load_error(file_path)
            if error is not None:
                raise error
        resolution = page._resolve_page_body(
            file_path, active_module, request, **kwargs
        )
        if resolution.http_response is not None:
            return resolution.http_response
        shaper = partial_shaper_slot.get()
        intent = shaper.intent(request)
        if intent.zones:
            return shaper.zone_response(
                file_path,
                request,
                intent,
                dynamic_body=resolution.dynamic,
                url_kwargs=dict(kwargs),
            )
        body = resolution.body if resolution.body is not None else ""
        return HttpResponse(page._render_composed(file_path, body, request, **kwargs))

    return view


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
    module = _load_python_module_memo(file_path)
    if module is None:
        if last_load_error(file_path) is None:
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
    # at module level would close the next.pages <-> next.urls cycle.
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


__all__ = ["create_url_pattern", "unified_view"]
