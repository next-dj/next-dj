"""Filesystem-root helpers for build tooling and symlink management.

Distinct from watch specs, these return a canonical directory list for downstream
tooling (dockerfiles, editors, symlink builders), without reloader semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.components import component_watch_roots
from next.pages.watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from pathlib import Path


def get_framework_filesystem_roots_for_linking() -> list[Path]:
    """Return sorted unique roots from page trees and component backends.

    The page trees arrive resolved, so only the component roots are normalised.
    """
    roots: set[Path] = set(get_pages_directories_for_watch())
    roots.update(root.resolve() for root in component_watch_roots())
    return sorted(roots)
