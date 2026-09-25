"""The `sitemap` declaration object and the façade over the discovered SEO sources."""

from __future__ import annotations

import enum
import itertools
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from next.urls.manager import router_manager

from .discovery import SeoRoot, discover_seo_roots
from .registry import sitemap_items_registry
from .robots import RobotsSource, robots_candidates
from .sitemaps import RouteSitemap, SitemapOptions, serves_sitemap


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest


logger = logging.getLogger(__name__)

_version_counter = itertools.count(1)
"""Process-wide source of manager versions, so no two managers share one."""


class SitemapDeclaration:
    """The `sitemap` object a `sitemap.py` decorates its items callables with."""

    def items[F: Callable[..., Any]](self, trail: str) -> Callable[[F], F]:
        """Register the decorated callable as the entries of `trail` in this tree.

        The tree is the one of the running `sitemap.py`, wherever the callable lives.
        """
        registered_from = Path(sys._getframe(1).f_code.co_filename)

        def register(func: F) -> F:
            sitemap_items_registry.register(registered_from, trail, func)
            return func

        return register


sitemap = SitemapDeclaration()


class _Unset(enum.Enum):
    UNSET = enum.auto()


class SeoManager:
    """Memoise the SEO sources of the routed page trees until a reset."""

    def __init__(self) -> None:
        """Start with nothing discovered."""
        self._version = next(_version_counter)
        self._roots: tuple[SeoRoot, ...] | None = None
        self._robots: RobotsSource | Literal[_Unset.UNSET] | None = _Unset.UNSET

    @property
    def version(self) -> int:
        """Cache token moved by every reset, keying the wrapped sitemap view."""
        return self._version

    def reset(self, **kwargs) -> None:
        """Drop the discovered roots and the robots source, moving the version.

        The items registry stays, as a reload never re-runs a memoised `sitemap.py`.
        """
        self._version = next(_version_counter)
        self._roots = None
        self._robots = _Unset.UNSET

    def roots(self) -> tuple[SeoRoot, ...]:
        """Return every routed page tree with its SEO sources loaded."""
        roots = self._roots
        if roots is None:
            roots = self._roots = discover_seo_roots(router_manager)
        return roots

    def has_sitemap(self) -> bool:
        """Whether the project serves a sitemap, which `NOINDEX` turns off."""
        return serves_sitemap(self.roots())

    def sitemaps(self, request: HttpRequest | None = None) -> dict[str, RouteSitemap]:
        """Return a fresh `RouteSitemap` per declaring tree, keyed by section."""
        if not self.has_sitemap():
            return {}
        return {
            root.section: RouteSitemap(root, module, request=request)
            for root in self.roots()
            if (module := root.sitemap_module) is not None
        }

    def cache_seconds(self) -> int | None:
        """Return the shortest `cache` a `sitemap.py` declares, `None` without one."""
        declared = [
            seconds
            for root in self.roots()
            if (module := root.sitemap_module) is not None
            and (seconds := SitemapOptions.read(module).cache) is not None
        ]
        return min(declared, default=None)

    def robots_source(self) -> RobotsSource | None:
        """Return the one `/robots.txt` source, warning once about the rest."""
        held = self._robots
        if held is _Unset.UNSET:
            held = self._robots = self._select_robots()
        return held

    def _select_robots(self) -> RobotsSource | None:
        candidates = robots_candidates(self.roots())
        if len(candidates) > 1:
            logger.warning(
                "/robots.txt has %d sources and %s serves it, the rest are ignored: %s",
                len(candidates),
                candidates[0][0],
                ", ".join(str(path) for path, _served in candidates[1:]),
            )
        return next(
            (served for _path, served in candidates if served is not None), None
        )


seo_manager = SeoManager()


def reset_seo_sources() -> None:
    """Drop the discovered SEO sources and every `@sitemap.items` registration."""
    seo_manager.reset()
    sitemap_items_registry.reset()


__all__ = [
    "SeoManager",
    "SitemapDeclaration",
    "reset_seo_sources",
    "seo_manager",
    "sitemap",
]
