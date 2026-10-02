"""Discovery of the SEO sources at the top of every routed page tree.

The sources load through their own loader, so a broken one never marks a page broken.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from next.discovery import routed_page_trees
from next.pages.responses import cache_control
from next.urls.manager import router_manager
from next.utils import (
    TreeSource,
    load_tree_source,
    stat_mtime_ns,
    tree_label,
    unique_labels,
    walk_page_tree,
)

from .errors import SeoSourceImportError
from .registry import sitemap_items_registry


if TYPE_CHECKING:
    import types
    from collections.abc import Mapping
    from pathlib import Path

    from next.pages.responses import CacheControl
    from next.urls import RouterManager
    from next.utils import PageRoot

    from .registry import SitemapItemsEntry


SITEMAP_MODULE: Final = "sitemap.py"
ROBOTS_MODULE: Final = "robots.py"
ROBOTS_FILE: Final = "robots.txt"
SOURCE_NAMES: Final = (SITEMAP_MODULE, ROBOTS_MODULE, ROBOTS_FILE)
"""Every file the top of a page tree may hold for the SEO routes."""

SLUG: Final = re.compile(r"[-a-zA-Z0-9_]+")
"""What a sitemap section name matches, the Django `slug` converter."""


SeoSource = TreeSource[SeoSourceImportError]
"""One Python SEO source with the module it loaded to, or its import failure."""


@dataclass(frozen=True, slots=True)
class BrokenSource:
    """A Python source that failed to import, keeping its route to answer 404.

    Falling back to another source would serve rules nobody wrote.
    """

    path: Path


@dataclass(frozen=True, slots=True)
class SeoRoot:
    """One page tree with its routed trails and the SEO sources at its top."""

    root: PageRoot
    label: str
    section: str
    trails: Mapping[str, Path]
    sitemap: SeoSource | None = None
    robots: SeoSource | None = None
    robots_file: Path | None = None
    skip_names: frozenset[str] = frozenset()
    stamps: tuple[int | None, ...] = ()

    @property
    def path(self) -> Path:
        """Return the page tree the sources sit in."""
        return self.root.path

    @property
    def sitemap_path(self) -> Path:
        """Return where the `sitemap.py` of the tree sits, present or not."""
        return self.path / SITEMAP_MODULE

    @property
    def sitemap_module(self) -> types.ModuleType | None:
        """Return the `sitemap.py` of the tree when one imported."""
        return None if self.sitemap is None else self.sitemap.module

    @property
    def robots_module(self) -> types.ModuleType | None:
        """Return the `robots.py` of the tree when one imported."""
        return None if self.robots is None else self.robots.module

    def items_entries(self) -> tuple[SitemapItemsEntry, ...]:
        """Return every `@sitemap.items` the `sitemap.py` of the tree ran."""
        return sitemap_items_registry.entries_for(self.sitemap_path)

    def stale(self) -> bool:
        """Whether a source file appeared, went or moved on disk since the discovery."""
        return source_stamps(self.path) != self.stamps


def source_stamps(tree: Path) -> tuple[int | None, ...]:
    """Return the mtime of each SEO source at the top of `tree`, `None` where absent."""
    return tuple(stat_mtime_ns(tree / name) for name in SOURCE_NAMES)


def load_source(path: Path) -> SeoSource | None:
    """Execute the source at `path`, or answer `None` when no file sits there.

    A failure stays on the source for the checks to report, so the routes still build.
    """
    if path.is_file():
        sitemap_items_registry.forget(path)
    return load_tree_source(path, f"next_seo_{path.stem}", SeoSourceImportError)


def declared_section(module: types.ModuleType | None) -> str | None:
    """Return the `section` a `sitemap.py` names its tree by, when it is a slug."""
    value = getattr(module, "section", None)
    return value if isinstance(value, str) and SLUG.fullmatch(value) else None


def declared_cache(module: types.ModuleType) -> CacheControl | None:
    """Return the `cache` a source module declares, in any form a page takes but one.

    A callable is left out, since the response is cached before a request calls it.
    """
    value = getattr(module, "cache", None)
    return None if callable(value) else cache_control(value)


def section_label(path: Path, module: types.ModuleType | None = None) -> str:
    """Return the section a page tree is addressed by in the sitemap index.

    The `section` of its `sitemap.py` wins, then the app label, then the directory.
    """
    return declared_section(module) or tree_label(path)


def _file(path: Path) -> Path | None:
    return path if path.is_file() else None


def discover_seo_roots(manager: RouterManager) -> tuple[SeoRoot, ...]:
    """Return every page tree in router order, each with its SEO sources loaded."""
    found = routed_page_trees(manager)
    stamps = [source_stamps(root.path) for root, _skip in found]
    sitemaps = [load_source(root.path / SITEMAP_MODULE) for root, _skip in found]
    labels = [
        section_label(root.path, None if source is None else source.module)
        for (root, _skip), source in zip(found, sitemaps, strict=True)
    ]
    return tuple(
        SeoRoot(
            root=root,
            label=label,
            section=section,
            trails=dict(sorted(walk_page_tree(root.path, skip_names))),
            sitemap=sitemap,
            robots=load_source(root.path / ROBOTS_MODULE),
            robots_file=_file(root.path / ROBOTS_FILE),
            skip_names=skip_names,
            stamps=stamp,
        )
        for (root, skip_names), stamp, sitemap, label, section in zip(
            found, stamps, sitemaps, labels, unique_labels(labels), strict=True
        )
    )


@functools.cache
def page_tree_roots() -> tuple[SeoRoot, ...]:
    """Return every page tree the URL router serves, discovered once per reset."""
    return discover_seo_roots(router_manager)


def forget_page_tree_roots() -> None:
    """Drop the discovered trees, so the next read loads every source again."""
    page_tree_roots.cache_clear()


__all__ = [
    "ROBOTS_FILE",
    "ROBOTS_MODULE",
    "SITEMAP_MODULE",
    "SLUG",
    "SOURCE_NAMES",
    "BrokenSource",
    "SeoRoot",
    "SeoSource",
    "declared_cache",
    "declared_section",
    "discover_seo_roots",
    "forget_page_tree_roots",
    "load_source",
    "page_tree_roots",
    "section_label",
    "source_stamps",
]
