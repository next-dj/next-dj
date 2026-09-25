"""Discovery of the `sitemap.py`, `robots.py` and `robots.txt` of every page tree."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from django.apps import apps
from django.utils.text import slugify

from next.discovery import first_visit, get_page_roots, page_tree_skip_names
from next.pages.loaders import load_page_module


if TYPE_CHECKING:
    import types

    from next.urls import RouterManager
    from next.utils import PageRoot


logger = logging.getLogger(__name__)

SITEMAP_MODULE = "sitemap.py"
ROBOTS_MODULE = "robots.py"
ROBOTS_FILE = "robots.txt"


@dataclass(frozen=True, slots=True)
class SeoRoot:
    """One page tree with the SEO sources found at its top."""

    root: PageRoot
    section: str
    sitemap_module: Path | None
    robots_module: Path | None
    robots_file: Path | None
    skip_names: frozenset[str] = frozenset()

    @property
    def path(self) -> Path:
        """Return the page tree the sources sit in."""
        return self.root.path


def load_seo_module(file_path: Path) -> types.ModuleType | None:
    """Return the module at `file_path`, `None` when it is absent or fails to import.

    The failure is logged here and reported by the checks, so the routes still build.
    """
    module, error = load_page_module(file_path)
    if error is not None:
        logger.warning("%s failed to import, so it declares nothing", file_path)
        return None
    return module


def _probe_module(file_path: Path) -> Path | None:
    """Return `file_path` when a module lives there and imports."""
    return None if load_seo_module(file_path) is None else file_path


def _probe_file(file_path: Path) -> Path | None:
    """Return `file_path` when a regular file lives there."""
    return file_path if file_path.is_file() else None


def _app_label_for(path: Path) -> str | None:
    """Return the label of the installed app whose directory holds `path`."""
    resolved = path.resolve()
    for config in apps.get_app_configs():
        if resolved.is_relative_to(Path(config.path).resolve()):
            return str(config.label)
    return None


def section_label(path: Path) -> str:
    """Return the section a page tree is addressed by in the sitemap index."""
    label = _app_label_for(path)
    if label is None:
        label = slugify(path.name) or "root"
    return label


def _unique_section(label: str, taken: dict[str, int]) -> str:
    """Return `label` suffixed past the copies already handed out."""
    count = taken.get(label, 0) + 1
    taken[label] = count
    return label if count == 1 else f"{label}-{count}"


def discover_seo_roots(router_manager: RouterManager) -> tuple[SeoRoot, ...]:
    """Return every page tree in router order, each probed for its SEO sources."""
    seen: set[Path] = set()
    taken: dict[str, int] = {}
    roots: list[SeoRoot] = []
    for router in router_manager.backends:
        skip_names = page_tree_skip_names(router)
        for root in get_page_roots(router):
            if not first_visit(root.path, seen):
                continue
            roots.append(
                SeoRoot(
                    root=root,
                    section=_unique_section(section_label(root.path), taken),
                    sitemap_module=_probe_module(root.path / SITEMAP_MODULE),
                    robots_module=_probe_module(root.path / ROBOTS_MODULE),
                    robots_file=_probe_file(root.path / ROBOTS_FILE),
                    skip_names=skip_names,
                )
            )
    return tuple(roots)


__all__ = [
    "ROBOTS_FILE",
    "ROBOTS_MODULE",
    "SITEMAP_MODULE",
    "SeoRoot",
    "discover_seo_roots",
    "load_seo_module",
    "section_label",
]
