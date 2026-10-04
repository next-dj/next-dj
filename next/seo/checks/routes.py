"""System checks on the addresses the SEO routes answer."""

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
from next.seo.routes import ROBOTS_ROUTE, SITEMAP_ROUTE
from next.seo.views import robots_view, sitemap_view

from .roots import loaded_seo_roots, serves_robots, serves_sitemap


if TYPE_CHECKING:
    from collections.abc import Callable

    from next.seo.discovery import SeoRoot


_SECTION_TRAIL: Final = re.compile(r"sitemap-[-a-zA-Z0-9_]+\.xml")


def _resolved(url: str) -> ResolverMatch | None:
    """Return what `ROOT_URLCONF` resolves `url` to, or `None` for a miss."""
    try:
        return resolve(url)
    except Resolver404:
        return None


def _served(roots: tuple[SeoRoot, ...]) -> list[tuple[str, Callable[..., Any], str]]:
    """Return the served SEO paths, each with its view and a source description."""
    served: list[tuple[str, Callable[..., Any], str]] = []
    if serves_sitemap():
        served.append((SITEMAP_ROUTE, sitemap_view, "a sitemap backend"))
    if serves_robots(roots):
        served.append((ROBOTS_ROUTE, robots_view, "a robots source"))
    return served


def _trail_address(trail: str, served: list[str]) -> str | None:
    """Return the served SEO path a page trail collides with, or `None`."""
    if trail in served:
        return f"/{trail}"
    if SITEMAP_ROUTE in served and _SECTION_TRAIL.fullmatch(trail) is not None:
        return f"/{trail}"
    return None


@register(Tags.urls, NEXT, SEO)
def check_seo_route_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Flag a page routed at an address the SEO sources serve (`next.E115`)."""
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
    routes = [route for route, _view, _source in _served(roots)]
    for root in roots:
        for trail, page_path in root.trails.items():
            address = _trail_address(trail, routes)
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
    return errors


@register(Tags.urls, NEXT, SEO)
def check_seo_routes_at_host_root(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a served SEO path does not reach the framework view (`next.W094`).

    An include of `next.urls` under a prefix or `i18n_patterns()` moves the routes.
    """
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    for route, view, source in _served(roots):
        match = _resolved(f"/{route}")
        if match is not None and match.func is view:
            continue
        found = (
            "does not resolve" if match is None else f"resolves to {match.view_name}"
        )
        warnings.append(
            DjangoWarning(
                f"/{route} {found} under ROOT_URLCONF {urlconf!r} although {source} "
                "declares it, so crawlers never reach the framework route there. "
                "Mount include('next.urls') at the root of the URLconf, or "
                "include('next.seo.urls') there when it sits under a prefix or "
                "i18n_patterns(), ahead of any pattern of your own at that address.",
                obj=settings,
                id="next.W094",
            )
        )
    return warnings


__all__ = ["check_seo_route_collisions", "check_seo_routes_at_host_root"]
