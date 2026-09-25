"""The `sitemap` declaration object and the façade over the discovered SEO sources."""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any

from next.conf.signals import settings_reloaded
from next.introspect import defining_file
from next.urls.manager import router_manager

from .discovery import SeoRoot, discover_seo_roots, load_seo_module
from .registry import sitemap_items_registry
from .robots import RobotsFile, RobotsSource, rules_from_module
from .sitemaps import RouteSitemap


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest


logger = logging.getLogger(__name__)

_version_counter = itertools.count(1)
"""Process-wide source of manager versions, so no two managers share one."""


class SitemapDeclaration:
    """The `sitemap` object a `sitemap.py` decorates its items callables with."""

    def items[F: Callable[..., Any]](self, trail: str) -> Callable[[F], F]:
        """Register the decorated callable as the entries of `trail` in this tree."""

        def register(func: F) -> F:
            sitemap_items_registry.register(defining_file(func).parent, trail, func)
            return func

        return register


sitemap = SitemapDeclaration()


class SeoManager:
    """Memoise the SEO sources of the routed page trees until a reset."""

    def __init__(self) -> None:
        """Start with nothing discovered."""
        self._version = next(_version_counter)
        self._roots: tuple[SeoRoot, ...] | None = None
        self._robots: tuple[RobotsSource | None] | None = None

    @property
    def version(self) -> int:
        """Cache token moved by every reset, keying the lazy urlpatterns."""
        return self._version

    def reset(self, **kwargs) -> None:
        """Drop the discovered roots and the robots source, moving the version.

        The items registry stays, because a memoised `sitemap.py` is not re-executed
        by a reload and its registrations would be lost until the file moved on disk.
        """
        self._version = next(_version_counter)
        self._roots = None
        self._robots = None

    def roots(self) -> tuple[SeoRoot, ...]:
        """Return every routed page tree probed for its SEO sources."""
        roots = self._roots
        if roots is None:
            roots = self._roots = discover_seo_roots(router_manager)
        return roots

    def has_sitemap(self) -> bool:
        """Whether any page tree declares a `sitemap.py`."""
        return any(root.sitemap_module is not None for root in self.roots())

    def sitemaps(self, request: HttpRequest | None = None) -> dict[str, RouteSitemap]:
        """Return a fresh `RouteSitemap` per declaring tree, keyed by section."""
        sitemaps: dict[str, RouteSitemap] = {}
        for root in self.roots():
            if root.sitemap_module is None:
                continue
            module = load_seo_module(root.sitemap_module)
            if module is not None:
                sitemaps[root.section] = RouteSitemap(root, module, request=request)
        return sitemaps

    def cache_seconds(self) -> int | None:
        """Return the shortest `cache` a `sitemap.py` declares, `None` without one."""
        declared = [
            seconds
            for root in self.roots()
            if root.sitemap_module is not None
            and (module := load_seo_module(root.sitemap_module)) is not None
            and isinstance(seconds := getattr(module, "cache", None), int)
            and seconds > 0
        ]
        return min(declared, default=None)

    def robots_source(self) -> RobotsSource | None:
        """Return the one `/robots.txt` source, warning once about the rest."""
        held = self._robots
        if held is None:
            held = self._robots = (self._select_robots(),)
        return held[0]

    def _select_robots(self) -> RobotsSource | None:
        candidates = [
            path
            for root in self.roots()
            for path in (root.robots_module, root.robots_file)
            if path is not None
        ]
        if len(candidates) > 1:
            logger.warning(
                "/robots.txt has %d sources and %s serves it, the rest are ignored: %s",
                len(candidates),
                candidates[0],
                ", ".join(str(path) for path in candidates[1:]),
            )
        for root in self.roots():
            source = _robots_of(root)
            if source is not None:
                return source
        return None


def _robots_of(root: SeoRoot) -> RobotsSource | None:
    """Return the robots source of one tree, `robots.py` ahead of `robots.txt`."""
    if root.robots_module is not None:
        module = load_seo_module(root.robots_module)
        if module is not None:
            return rules_from_module(root.robots_module, module)
    if root.robots_file is not None:
        return RobotsFile(root.robots_file)
    return None


seo_manager = SeoManager()


settings_reloaded.connect(seo_manager.reset)


__all__ = ["SeoManager", "SitemapDeclaration", "seo_manager", "sitemap"]
