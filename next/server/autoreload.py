"""Custom `StatReloader` that also watches the discovered route set.

`.djx` templates are deliberately left out of the watch. Under `DEBUG` they are
re-read on render with mtime-based invalidation inside pages and components.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, override

from django.utils.autoreload import StatReloader

from next.pages.watch import get_pages_directories_for_watch
from next.urls.dispatcher import scan_pages_tree


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


logger = logging.getLogger(__name__)


def _tree_dir_signature(root: Path) -> tuple[float, int]:
    """Return `(max mtime, directory count)` across every subdirectory.

    `os.scandir` hands back a cached stat per `DirEntry`, sparing a second syscall, and
    the count catches two renames that preserve the latest mtime.
    """
    try:
        root_st = root.stat()
    except OSError:
        return (0.0, 0)
    latest = root_st.st_mtime
    count = 1
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            scanner = os.scandir(current)
        except OSError:
            continue
        with scanner as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                latest = max(latest, st.st_mtime)
                count += 1
                stack.append(entry.path)
    return (latest, count)


class NextStatReloader(StatReloader):
    """Reload on route set changes in addition to `.py` mtime changes."""

    def __init__(self) -> None:
        """Initialise the cached route set used for tick-to-tick diffs."""
        super().__init__()
        self._previous_routes: set[tuple[str, Path]] | None = None
        self._dir_signatures: dict[Path, tuple[float, int]] = {}
        self._cached_routes: set[tuple[str, Path]] | None = None

    def _check_routes(self, current: set[tuple[str, Path]]) -> None:
        """Notify the reloader when the discovered route set changed."""
        prev = self._previous_routes
        if prev is None or current == prev:
            self._previous_routes = current
            return
        # The sets differ, so one side of the difference always holds a route.
        diff = (current - prev) or (prev - current)
        self.notify_file_changed(next(iter(diff))[1])
        self._previous_routes = current

    def _collect_routes(self) -> set[tuple[str, Path]]:
        """Return the route set, reusing the cached value when signatures match."""
        pages_paths = get_pages_directories_for_watch()
        new_signatures = {p: _tree_dir_signature(p) for p in pages_paths}
        if self._cached_routes is not None and new_signatures == self._dir_signatures:
            return self._cached_routes
        routes = {
            (url_path, file_path.resolve())
            for pages_path in pages_paths
            for url_path, file_path in scan_pages_tree(pages_path)
        }
        self._dir_signatures = new_signatures
        self._cached_routes = routes
        return routes

    @override
    def tick(self) -> Generator[None, None, None]:
        """Recompute routes, compare to the previous tick, then delegate."""
        parent_ticker = super().tick()
        while True:
            try:
                routes = self._collect_routes()
            except (OSError, ImportError, ValueError) as e:
                # Narrow on purpose. The watch helpers already absorb what a
                # router raises, so a blind catch would hide a framework bug.
                logger.debug("next route set check skipped: %s", e)
            else:
                self._check_routes(routes)
            yield next(parent_ticker)
