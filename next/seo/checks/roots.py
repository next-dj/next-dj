"""Helpers giving the SEO checks the page trees the runtime discovery found."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.checks.common import get_router_manager
from next.seo.manager import seo_manager
from next.seo.robots import robots_candidates


if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from pathlib import Path

    from next.seo.discovery import SeoRoot


def loaded_seo_roots() -> tuple[SeoRoot, ...]:
    """Return every routed page tree with its sources, from the runtime discovery.

    Reusing the discovery executes no `sitemap.py` or `robots.py` a second time.
    A router that fails to start yields no tree, as `next.E007` already reports it.
    """
    router_manager, init_errors = get_router_manager()
    if router_manager is None or init_errors:
        return ()
    return seo_manager.roots()


def sitemap_roots(
    roots: tuple[SeoRoot, ...],
) -> Iterator[tuple[SeoRoot, types.ModuleType]]:
    """Yield every tree whose `sitemap.py` imported, paired with the module."""
    for root in roots:
        module = root.sitemap_module
        if module is not None:
            yield root, module


def robots_modules(
    roots: tuple[SeoRoot, ...],
) -> Iterator[tuple[Path, types.ModuleType]]:
    """Yield the path and the module of every `robots.py` that imported."""
    for root in roots:
        if root.robots is not None and root.robots.module is not None:
            yield root.robots.path, root.robots.module


def declares_sitemap(roots: tuple[SeoRoot, ...]) -> bool:
    """Whether any tree has a `sitemap.py`, imported or not."""
    return any(root.sitemap is not None for root in roots)


def serves_sitemap() -> bool:
    """Whether `/sitemap.xml` is mounted, as the route decides it."""
    return seo_manager.serves_sitemap()


def serves_robots(roots: tuple[SeoRoot, ...]) -> bool:
    """Whether a robots source serves `/robots.txt`, as the route decides it."""
    return any(served is not None for _path, served in robots_candidates(roots))


def published_sources(roots: tuple[SeoRoot, ...]) -> list[str]:
    """Return the names of the crawler-facing sources the site serves."""
    served = (("a sitemap", serves_sitemap()), ("a robots.txt", serves_robots(roots)))
    return [name for name, present in served if present]


def items_trails(root: SeoRoot) -> set[str]:
    """Return the trails the `@sitemap.items` callables of the tree list URLs for."""
    return {entry.trail for entry in root.items_entries()}


__all__ = [
    "declares_sitemap",
    "items_trails",
    "loaded_seo_roots",
    "published_sources",
    "robots_modules",
    "serves_robots",
    "serves_sitemap",
    "sitemap_roots",
]
