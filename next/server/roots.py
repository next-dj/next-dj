"""Filesystem-root helpers for build tooling and symlink management.

These helpers are distinct from watch specs. They return a canonical
list of directories that downstream tooling (dockerfiles, editors,
symlink builders) needs, without reloader semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.components import components_manager
from next.pages.watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from pathlib import Path


def get_framework_filesystem_roots_for_linking() -> list[Path]:
    """Return sorted unique roots from page trees and component backends.

    The page trees arrive resolved, so only the component roots are normalised.
    """
    roots: set[Path] = set(get_pages_directories_for_watch())
    for backend in components_manager.backends:
        roots.update(root.resolve() for root in backend.watch_roots())
    return sorted(roots)
