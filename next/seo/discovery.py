"""Discovery of the SEO sources at the top of every routed page tree.

The sources use their own loader, so an import failure never flags a page as broken.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from django.apps import apps
from django.utils.text import slugify

from next.discovery import routed_page_trees
from next.pages.responses import cache_control
from next.urls.manager import router_manager, seo_routes_version
from next.utils import TreeSource, load_tree_source, stat_mtime_ns, walk_page_tree

from .errors import SeoSourceImportError
from .registry import sitemap_items_registry


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Mapping

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
"""The pattern of a sitemap section name, the one of the Django `slug` converter."""


SeoSource = TreeSource[SeoSourceImportError]
"""One Python SEO source with its loaded module or its import error."""


@dataclass(frozen=True, slots=True)
class BrokenSource:
    """A Python source that failed to import, whose route stays mounted.

    Falling back to another source would serve rules the project did not declare.
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
        """Return the directory of the page tree."""
        return self.root.path

    @property
    def sitemap_path(self) -> Path:
        """Return the path of the `sitemap.py` of the tree, whether it exists or not."""
        return self.path / SITEMAP_MODULE

    @property
    def sitemap_module(self) -> types.ModuleType | None:
        """Return the imported `sitemap.py` module of the tree, or `None`."""
        return None if self.sitemap is None else self.sitemap.module

    @property
    def robots_module(self) -> types.ModuleType | None:
        """Return the imported `robots.py` module of the tree, or `None`."""
        return None if self.robots is None else self.robots.module

    def items_entries(self) -> tuple[SitemapItemsEntry, ...]:
        """Return the `@sitemap.items` entries the `sitemap.py` of the tree ran."""
        return sitemap_items_registry.entries_for(self.sitemap_path)

    def stale(self) -> bool:
        """Whether a source file was created, deleted or modified since discovery."""
        return source_stamps(self.path) != self.stamps


def source_stamps(tree: Path) -> tuple[int | None, ...]:
    """Return the mtime of each SEO source at the top of `tree`, `None` where absent."""
    return tuple(stat_mtime_ns(tree / name) for name in SOURCE_NAMES)


def load_source(path: Path) -> SeoSource | None:
    """Execute the source at `path`, or return `None` when the file does not exist.

    An import error is kept on the result for the checks, so the routes still build.
    """
    if path.is_file():
        sitemap_items_registry.forget(path)
    return load_tree_source(path, f"next_seo_{path.stem}", SeoSourceImportError)


def declared_section(module: types.ModuleType | None) -> str | None:
    """Return the `section` a `sitemap.py` names its tree by, when it is a slug."""
    value = getattr(module, "section", None)
    return value if isinstance(value, str) and SLUG.fullmatch(value) else None


def declared_cache(module: types.ModuleType) -> CacheControl | None:
    """Return the `cache` a source module declares, in any static form a page accepts.

    A callable is ignored, since the cache wrapper is built before any request.
    """
    value = getattr(module, "cache", None)
    return None if callable(value) else cache_control(value)


def _app_label_for(path: Path) -> str | None:
    """Return the label of the innermost installed app whose directory holds `path`."""
    resolved = path.resolve()
    holding = [
        (len(app_path.parts), str(config.label))
        for config in apps.get_app_configs()
        if resolved.is_relative_to(app_path := Path(config.path).resolve())
    ]
    return max(holding)[1] if holding else None


def tree_label(path: Path) -> str:
    """Return the stable name of a page tree, its app label or else its directory."""
    label = _app_label_for(path)
    if label is None:
        label = slugify(path.name) or "root"
    return label


def unique_labels(labels: Iterable[str]) -> list[str]:
    """Return the labels made distinct, each repeat suffixed with a free number.

    A label no other tree shares is kept, and a suffixed label never equals an input.
    """
    wanted = list(labels)
    taken = set(wanted)
    unique: list[str] = []
    handed: set[str] = set()
    for label in wanted:
        candidate = label
        number = 1
        while candidate in handed or (candidate != label and candidate in taken):
            number += 1
            candidate = f"{label}-{number}"
        handed.add(candidate)
        unique.append(candidate)
    return unique


def section_label(path: Path, module: types.ModuleType | None = None) -> str:
    """Return the section name of a page tree in the sitemap index.

    The `section` of its `sitemap.py` comes first, then the app label, then the name.
    """
    return declared_section(module) or tree_label(path)


def _file(path: Path) -> Path | None:
    return path if path.is_file() else None


def discover_seo_roots(manager: RouterManager) -> tuple[SeoRoot, ...]:
    """Return every page tree in router order, each with its SEO sources loaded.

    The trails are sorted, so their order never depends on the directory listing.
    """
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


class _RootsMemo:
    """The discovered trees, tagged with the SEO routes version they were built at."""

    __slots__ = ("held", "lock")

    def __init__(self) -> None:
        """Start empty, with the lock that serialises discovery."""
        self.held: tuple[int, tuple[SeoRoot, ...]] | None = None
        self.lock = threading.RLock()

    def current(self, version: int) -> tuple[SeoRoot, ...] | None:
        """Return the trees discovered at `version`, `None` for another version."""
        held = self.held
        return held[1] if held is not None and held[0] == version else None


_roots_memo: Final = _RootsMemo()


def page_tree_roots() -> tuple[SeoRoot, ...]:
    """Return every page tree the URL router serves, discovered once per routes version.

    Discovery runs under a lock, so concurrent callers execute each source once. Trees
    discovered while a reset moves the version are returned but never served again.
    """
    roots = _roots_memo.current(seo_routes_version.value)
    if roots is not None:
        return roots
    with _roots_memo.lock:
        version = seo_routes_version.value
        roots = _roots_memo.current(version)
        if roots is None:
            roots = discover_seo_roots(router_manager)
            _roots_memo.held = (version, roots)
        return roots


def forget_page_tree_roots() -> None:
    """Clear the discovered trees, so the next call loads every source again."""
    with _roots_memo.lock:
        _roots_memo.held = None


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
