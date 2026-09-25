"""Discovery of the `sitemap.py`, `robots.py` and `robots.txt` of every page tree."""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from django.apps import apps
from django.utils.text import slugify

from next.discovery import first_visit, get_page_roots, page_tree_skip_names
from next.pages.loaders import load_page_module
from next.utils import walk_page_tree

from .registry import sitemap_items_registry


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterable, Mapping

    from next.pages.errors import PageModuleImportError
    from next.urls import RouterManager
    from next.utils import PageRoot


logger = logging.getLogger(__name__)

SITEMAP_MODULE = "sitemap.py"
ROBOTS_MODULE = "robots.py"
ROBOTS_FILE = "robots.txt"
SOURCE_NAMES: Final = (SITEMAP_MODULE, ROBOTS_MODULE, ROBOTS_FILE)


@dataclass(frozen=True, slots=True)
class SeoSource:
    """One `sitemap.py` or `robots.py` with the module it loaded to, or its failure."""

    path: Path
    module: types.ModuleType | None
    error: PageModuleImportError | None


@dataclass(frozen=True)
class SeoRoot:
    """One page tree with the SEO sources found at its top, loaded once."""

    root: PageRoot
    label: str
    section: str
    sitemap: SeoSource | None
    robots: SeoSource | None
    robots_file: Path | None
    skip_names: frozenset[str] = frozenset()

    @property
    def path(self) -> Path:
        """Return the page tree the sources sit in."""
        return self.root.path

    @property
    def sitemap_path(self) -> Path:
        """Return where the `sitemap.py` of the tree sits, present or not."""
        return self.path / SITEMAP_MODULE

    @functools.cached_property
    def trails(self) -> Mapping[str, Path]:
        """Return the page file of every trail in the tree, walked once."""
        return dict(walk_page_tree(self.path, self.skip_names))

    @property
    def sitemap_module(self) -> types.ModuleType | None:
        """Return the `sitemap.py` of the tree when one imported."""
        return None if self.sitemap is None else self.sitemap.module

    @property
    def robots_module(self) -> types.ModuleType | None:
        """Return the `robots.py` of the tree when one imported."""
        return None if self.robots is None else self.robots.module

    def items_entries(self) -> tuple[tuple[str, Callable[..., Any]], ...]:
        """Return `(trail, func)` for every `@sitemap.items` the `sitemap.py` ran."""
        return sitemap_items_registry.entries_for(self.sitemap_path)


def load_seo_source(file_path: Path) -> SeoSource | None:
    """Load the module at `file_path`, or answer `None` when no file sits there.

    A failure stays on the source for the checks to report, so the routes still build.
    """
    if not file_path.is_file():
        return None
    module, error = load_page_module(file_path)
    if error is not None:
        logger.warning("%s failed to import, so it declares nothing", file_path)
    return SeoSource(file_path, module, error)


def _app_label_for(path: Path) -> str | None:
    """Return the label of the innermost installed app whose directory holds `path`."""
    resolved = path.resolve()
    holding = [
        (len(app_path.parts), str(config.label))
        for config in apps.get_app_configs()
        if resolved.is_relative_to(app_path := Path(config.path).resolve())
    ]
    return max(holding)[1] if holding else None


def section_label(path: Path) -> str:
    """Return the section a page tree is addressed by in the sitemap index."""
    label = _app_label_for(path)
    if label is None:
        label = slugify(path.name) or "root"
    return label


def unique_sections(labels: Iterable[str]) -> list[str]:
    """Return one distinct section per label, a repeat suffixed past every label taken.

    A label no other tree shares keeps it, so a suffix never lands on a natural label.
    """
    wanted = list(labels)
    taken = set(wanted)
    sections: list[str] = []
    handed: set[str] = set()
    for label in wanted:
        section = label
        number = 1
        while section in handed or (section != label and section in taken):
            number += 1
            section = f"{label}-{number}"
        handed.add(section)
        sections.append(section)
    return sections


def discover_seo_roots(router_manager: RouterManager) -> tuple[SeoRoot, ...]:
    """Return every page tree in router order, each with its SEO sources loaded."""
    seen: set[Path] = set()
    found: list[tuple[PageRoot, frozenset[str]]] = []
    for router in router_manager.backends:
        skip_names = page_tree_skip_names(router)
        found.extend(
            (root, skip_names)
            for root in get_page_roots(router)
            if first_visit(root.path, seen)
        )
    labels = [section_label(root.path) for root, _skip in found]
    roots: list[SeoRoot] = []
    for (root, skip_names), label, section in zip(
        found, labels, unique_sections(labels), strict=True
    ):
        robots_file = root.path / ROBOTS_FILE
        roots.append(
            SeoRoot(
                root=root,
                label=label,
                section=section,
                sitemap=load_seo_source(root.path / SITEMAP_MODULE),
                robots=load_seo_source(root.path / ROBOTS_MODULE),
                robots_file=robots_file if robots_file.is_file() else None,
                skip_names=skip_names,
            )
        )
    return tuple(roots)


__all__ = [
    "ROBOTS_FILE",
    "ROBOTS_MODULE",
    "SITEMAP_MODULE",
    "SOURCE_NAMES",
    "SeoRoot",
    "SeoSource",
    "discover_seo_roots",
    "load_seo_source",
    "section_label",
    "unique_sections",
]
