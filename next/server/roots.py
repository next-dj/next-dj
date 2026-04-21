"""Filesystem-root helpers for build tooling and symlink management.

These helpers are distinct from watch specs. They return a canonical
list of directories that downstream tooling (dockerfiles, editors,
symlink builders) needs, without reloader semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.components import component_extra_roots_from_config
from next.conf import next_framework_settings
from next.pages.watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from pathlib import Path


def get_framework_filesystem_roots_for_linking() -> list[Path]:
    """Return sorted unique roots from page trees and component `DIRS`."""
    roots: set[Path] = {p.resolve() for p in get_pages_directories_for_watch()}
    comp_cfgs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if isinstance(comp_cfgs, list):
        for config in comp_cfgs:
            if isinstance(config, dict):
                roots.update(
                    p.resolve() for p in component_extra_roots_from_config(config)
                )
    return sorted(roots)
