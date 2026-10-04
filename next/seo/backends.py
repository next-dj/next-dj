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
    """Return the sort key ordering no-store first, then by age, with no age last."""
    seconds = control.seconds
    return control.stores, math.inf if seconds is None else seconds


def shortest_cache(controls: Iterable[CacheControl | None]) -> CacheControl | None:
    """Return the cache control with the shortest lifetime, no-store first."""
    return min(
        (control for control in controls if control is not None),
        key=_freshness,
        default=None,
    )


def backend_path(backend: object) -> str:
    """Return the dotted path of the class of `backend`, for log and check messages."""
    kind = type(backend)
    return f"{kind.__module__}.{kind.__qualname__}"


class SitemapBackend(ABC):
    """One source of sitemap sections, configured by a `SITEMAP_BACKENDS` entry."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Store the entry and its `OPTIONS` mapping."""
        self.config = config
        self.options: Mapping[str, Any] = config.get("OPTIONS") or {}

    @abstractmethod
    def sections(self, request: HttpRequest | None) -> Mapping[str, Sitemap[Any]]:
        """Return new Django sitemaps by slug, `request` being `None` in a check."""

    def serves(self) -> bool:
        """Whether the backend has sections to serve, which mounts `/sitemap.xml`."""
        return True

    def cache_control(self) -> CacheControl | None:
        """Return the cache control of the sitemap responses, `None` for no caching."""
        return None


class PageTreeSitemapBackend(SitemapBackend):
    """Serve the sections the `sitemap.py` at the top of each routed tree declares."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Create the backend with an empty static trail memo."""
        super().__init__(config)
        self._static: dict[Path, tuple[int, tuple[str, ...]]] = {}

    def roots(self) -> tuple[SeoRoot, ...]:
        """Return the routed page trees with their sources."""
        return page_tree_roots()

    @override
    def serves(self) -> bool:
        """Whether a page tree has a `sitemap.py`, including one failing to import."""
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
        """Return one section per tree plus one per `section=` its items name.

        Items on a trail `exclude` matches are dropped, and so is a static route that
        an items callable claims.
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
        """Return the static trails of `root` that no items callable claims.

        The noindex test resolves the metadata of every page, so the result is
        memoised per module version.
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
