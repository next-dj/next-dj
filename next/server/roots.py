"""Filesystem-root helpers for build tooling and symlink management.

These helpers are distinct from watch specs. They return a canonical
list of directories that downstream tooling (dockerfiles, editors,
symlink builders) needs, without reloader semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.backends import backend_entries
from next.components import component_extra_roots_from_config
from next.pages.watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from pathlib import Path


def get_framework_filesystem_roots_for_linking() -> list[Path]:
    """Return sorted unique roots from page trees and component `DIRS`.

    The page trees arrive resolved, so only the component roots are normalised.
    """
    roots: set[Path] = set(get_pages_directories_for_watch())
    for config in backend_entries("COMPONENT_BACKENDS"):
        roots.update(p.resolve() for p in component_extra_roots_from_config(config))
    return sorted(roots)
