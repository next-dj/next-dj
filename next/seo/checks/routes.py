"""System checks on the addresses the SEO routes answer.

The ids are `next.E115` and `next.W099`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.urls import Resolver404, ResolverMatch, resolve

from next.checks import NEXT, SEO
from next.seo.sitemaps import serves_sitemap
from next.seo.views import robots_view, sitemap_view

from .robots import SITEMAP_URL
from .roots import loaded_seo_roots, serves_robots


if TYPE_CHECKING:
    from collections.abc import Callable

    from next.seo.discovery import SeoRoot


_ROBOTS_URL: Final = "/robots.txt"
_SECTION_TRAIL: Final = re.compile(r"sitemap-[-a-zA-Z0-9_]+\.xml")


def _resolved(url: str) -> ResolverMatch | None:
    """Return what `ROOT_URLCONF` resolves `url` to, or `None` for a miss."""
    try:
        return resolve(url)
    except Resolver404:
        return None


def _served(roots: tuple[SeoRoot, ...]) -> list[tuple[str, Callable[..., Any], str]]:
    """Return the URLs the sources call for, each with its view and its source."""
    served: list[tuple[str, Callable[..., Any], str]] = []
    if serves_sitemap(roots):
        served.append((SITEMAP_URL, sitemap_view, "a sitemap.py"))
    if serves_robots(roots):
        served.append((_ROBOTS_URL, robots_view, "a robots source"))
    return served


def _trail_address(trail: str, served: list[str]) -> str | None:
    """Return the served address a page trail takes, or `None` when it takes none."""
    if SITEMAP_URL in served and (
        trail == "sitemap.xml" or _SECTION_TRAIL.fullmatch(trail) is not None
    ):
        return f"/{trail}"
    if trail == "robots.txt" and _ROBOTS_URL in served:
        return _ROBOTS_URL
    return None


@register(Tags.urls, NEXT, SEO)
def check_seo_route_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Flag a page or a urlpattern on an address the sources serve (`next.E115`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    served = _served(roots)
    urls = [url for url, _view, _source in served]
    for root in roots:
        for trail, page_path in root.trails.items():
            address = _trail_address(trail, urls)
            if address is None:
                continue
            errors.append(
                Error(
                    f"{page_path} routes {address}, the address the framework serves "
                    "from the SEO sources, so the two answers shadow each other. "
                    "Rename the directory.",
                    obj=str(page_path),
                    id="next.E115",
                )
            )
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    for url, view, _source in served:
        match = _resolved(url)
        if match is None or match.func is view:
            continue
        errors.append(
            Error(
                f"ROOT_URLCONF {urlconf!r} resolves {url} to {match.view_name}, "
                "ahead of the route the framework serves it at. Move that pattern "
                "below include('next.urls'), or drop it.",
                obj=settings,
                id="next.E115",
            )
        )
    return errors


@register(Tags.urls, NEXT, SEO)
def check_seo_routes_at_host_root(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a declared SEO route is not at the host root (`next.W099`).

    A `next.urls` include under a prefix or `i18n_patterns()` moves the routes with it.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    for url, _view, source in _served(roots):
        if _resolved(url) is not None:
            continue
        warnings.append(
            DjangoWarning(
                f"{url} does not resolve under ROOT_URLCONF {urlconf!r} although "
                f"{source} declares it, so crawlers find nothing at the host root. "
                "Mount include('next.seo.urls') at the root of the URLconf, outside "
                "any prefix and i18n_patterns().",
                obj=settings,
                id="next.W099",
            )
        )
    return warnings


__all__ = ["check_seo_route_collisions", "check_seo_routes_at_host_root"]
