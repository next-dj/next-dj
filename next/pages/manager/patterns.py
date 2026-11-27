"""The `URLPattern` a page view is mounted under and the sizing of page memos."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import URLPattern, path

from next.caches import PageCache
from next.conf import next_framework_settings


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from django.http.response import HttpResponseBase

    from next.urls import URLPatternParser

    from . import Page


def create_url_pattern(
    page: Page,
    url_path: str,
    file_path: Path,
    url_parser: URLPatternParser,
    page_view: Callable[[Page, Path], Callable[..., HttpResponseBase] | None],
) -> URLPattern | None:
    """Return a `path()` pattern mounting the view `page_view` builds for a page.

    The views sit above pages, so the router passes the factory in.
    """
    try:
        django_pattern, _parameters = url_parser.parse_url_pattern(url_path)
    except url_parser.parameter_error as exc:
        raise exc.with_file(file_path) from exc

    view = page_view(page, file_path)
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


__all__ = ["create_url_pattern", "fit_page_caches"]
