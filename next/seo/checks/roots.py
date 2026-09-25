"""The routed page trees the SEO checks read."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.checks.common import RunMemo, get_router_manager
from next.seo.discovery import SeoRoot, discover_seo_roots
from next.seo.robots import robots_candidates


if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from pathlib import Path

    from django.core.checks import CheckMessage


_seo_roots: RunMemo[tuple[SeoRoot, ...]] = RunMemo()


def loaded_seo_roots() -> tuple[list[CheckMessage], tuple[SeoRoot, ...]]:
    """Return every routed page tree as the runtime discovers it, once per run."""
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors, ()
    return init_errors, _seo_roots.get(
        router_manager, lambda: discover_seo_roots(router_manager)
    )


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
    """Whether any tree carries a `sitemap.py`, imported or not."""
    return any(root.sitemap is not None for root in roots)


def serves_robots(roots: tuple[SeoRoot, ...]) -> bool:
    """Whether a robots source serves `/robots.txt`, the way the route decides it."""
    return any(served is not None for _path, served in robots_candidates(roots))


def robots_paths(roots: tuple[SeoRoot, ...]) -> list[Path]:
    """Return every robots source in the order the route prefers them."""
    return [path for path, _served in robots_candidates(roots)]


def items_trails(root: SeoRoot) -> set[str]:
    """Return the trails `@sitemap.items` lists URLs of in the tree."""
    return {trail for trail, _func in root.items_entries()}


__all__ = [
    "declares_sitemap",
    "items_trails",
    "loaded_seo_roots",
    "robots_modules",
    "robots_paths",
    "serves_robots",
    "sitemap_roots",
]
