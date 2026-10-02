"""Pluggable sitemap backends, each contributing Django sitemaps keyed by section."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from next.pages.loaders import module_version

from .discovery import page_tree_roots
from .sitemaps import PageTreeSitemap, SitemapOptions, is_excluded, listed_trails


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from django.contrib.sitemaps import Sitemap
    from django.http import HttpRequest

    from next.pages.responses import CacheControl

    from .discovery import SeoRoot
    from .registry import SitemapItemsEntry


def _freshness(control: CacheControl) -> tuple[bool, float]:
    """Rank no store first, then the shortest age, a cache naming none ranking last."""
    seconds = control.seconds
    return control.stores, math.inf if seconds is None else seconds


def shortest_cache(controls: Iterable[CacheControl | None]) -> CacheControl | None:
    """Return the cache keeping a copy for the shortest time, no store ranking first."""
    return min(
        (control for control in controls if control is not None),
        key=_freshness,
        default=None,
    )


def backend_path(backend: object) -> str:
    """Return the dotted path of the class of `backend`, the name a report gives it."""
    kind = type(backend)
    return f"{kind.__module__}.{kind.__qualname__}"


class SitemapBackend(ABC):
    """One source of sitemap sections, configured by a `SITEMAP_BACKENDS` entry."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Keep the entry and its `OPTIONS`."""
        self.config = config
        self.options: Mapping[str, Any] = config.get("OPTIONS") or {}

    @abstractmethod
    def sections(self, request: HttpRequest | None) -> Mapping[str, Sitemap[Any]]:
        """Return fresh Django sitemaps by slug, `None` for the request of a check."""

    def serves(self) -> bool:
        """Whether the backend has sections to serve, so `/sitemap.xml` is routed."""
        return True

    def cache_control(self) -> CacheControl | None:
        """Return the cache the sitemap views carry, `None` for none at all."""
        return None


class PageTreeSitemapBackend(SitemapBackend):
    """The sections the `sitemap.py` at the top of each routed page tree declares."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Start with no static trails listed yet."""
        super().__init__(config)
        self._static: dict[Path, tuple[int, tuple[str, ...]]] = {}

    def roots(self) -> tuple[SeoRoot, ...]:
        """Return the routed page trees with their sources."""
        return page_tree_roots()

    @override
    def serves(self) -> bool:
        """Whether a page tree carries a `sitemap.py`, even one failing to import."""
        return any(root.sitemap is not None for root in self.roots())

    @override
    def cache_control(self) -> CacheControl | None:
        """Return the shortest `cache` a `sitemap.py` declares, `None` without one."""
        return shortest_cache(
            SitemapOptions.read(module).cache
            for root in self.roots()
            if (module := root.sitemap_module) is not None
        )

    @override
    def sections(self, request: HttpRequest | None) -> dict[str, Sitemap[Any]]:
        """Return a section per tree plus one per `section=` its items name.

        A trail an `exclude` glob covers drops its items, and a claimed trail its route.
        """
        sections: dict[str, Sitemap[Any]] = {}
        for root in self.roots():
            module = root.sitemap_module
            if module is None:
                continue
            options = SitemapOptions.read(module)
            grouped: dict[str, list[SitemapItemsEntry]] = {root.section: []}
            for entry in root.items_entries():
                if not is_excluded(entry.trail, options.exclude):
                    grouped.setdefault(entry.section or root.section, []).append(entry)
            for name, entries in grouped.items():
                static = (
                    self.static_trails(root, options) if name == root.section else ()
                )
                sections.setdefault(
                    name,
                    PageTreeSitemap(
                        root, options, static=static, items=entries, request=request
                    ),
                )
        return sections

    def static_trails(self, root: SeoRoot, options: SitemapOptions) -> tuple[str, ...]:
        """Return the static routes of `root` no items callable claims, memoised.

        Reading noindex folds a chain per page, so the list waits for a module reload.
        """
        version = module_version()
        held = self._static.get(root.path)
        if held is not None and held[0] == version:
            return held[1]
        claimed = {entry.trail for entry in root.items_entries()}
        trails = tuple(
            trail
            for trail in listed_trails(root.trails, options.exclude)
            if trail not in claimed
        )
        self._static[root.path] = (version, trails)
        return trails


__all__ = ["PageTreeSitemapBackend", "SitemapBackend", "backend_path", "shortest_cache"]
