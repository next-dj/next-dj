"""Custom `StatReloader` that also watches the discovered route set.

A `.djx` edit re-reads on render, so only a template that appears or goes restarts.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, override

from django.utils.autoreload import StatReloader

from next.components import component_watch_roots, refresh_component_files
from next.discovery import get_pages_directories_for_watch
from next.pages.loaders import has_body
from next.urls.dispatcher import scan_pages_tree


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from next.utils import InterceptPage


logger = logging.getLogger(__name__)

type _Signatures = dict[Path, tuple[float, int]]


def _tree_dir_signature(root: Path) -> tuple[float, int]:
    """Return `(max mtime, directory count)` across every subdirectory.

    The count catches two renames that preserve the latest mtime.
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


def _scan_with_intercepts(pages_path: Path) -> Iterator[tuple[str, Path]]:
    """Yield the routed pages of a tree, then its intercept pages by target trail."""
    found: list[InterceptPage] = []
    yield from scan_pages_tree(pages_path, on_intercept=found.append)
    for hit in found:
        yield hit.target_trail, hit.file


class NextStatReloader(StatReloader):
    """Reload on route set changes in addition to `.py` mtime changes."""

    def __init__(self) -> None:
        """Initialise the cached route set used for tick-to-tick diffs."""
        super().__init__()
        self._previous_routes: set[tuple[str, Path, bool]] | None = None
        self._dir_signatures: _Signatures = {}
        self._cached_routes: set[tuple[str, Path, bool]] | None = None
        self._component_shape: tuple[_Signatures, _Signatures] | None = None

    def _check_routes(self, current: set[tuple[str, Path, bool]]) -> None:
        """Notify the reloader when the discovered route set changed."""
        prev = self._previous_routes
        if prev is None or current == prev:
            self._previous_routes = current
            return
        # The sets differ, so one side of the difference always holds a route.
        diff = (current - prev) or (prev - current)
        self.notify_file_changed(next(iter(diff))[1])
        self._previous_routes = current

    def _collect_routes(self) -> set[tuple[str, Path, bool]]:
        """Return the routes and their loader bodies, cached while signatures match.

        A body in `page.py` changes with the file, which the `.py` watch restarts on.
        """
        pages_paths = get_pages_directories_for_watch()
        new_signatures = {p: _tree_dir_signature(p) for p in pages_paths}
        if self._cached_routes is not None and new_signatures == self._dir_signatures:
            return self._cached_routes
        routes = {
            (url_path, file_path.resolve(), has_body(file_path, None))
            for pages_path in pages_paths
            for url_path, file_path in _scan_with_intercepts(pages_path)
        }
        self._dir_signatures = new_signatures
        self._cached_routes = routes
        return routes

    def _refresh_components(self, pages: _Signatures | None) -> None:
        """Swap in the component boundary files once a watched tree changed shape.

        A boundary file that appears or goes moves the mtime of its directory.
        """
        shape = None
        if pages is not None:
            roots = component_watch_roots()
            shape = (pages, {root: _tree_dir_signature(root) for root in roots})
            if shape == self._component_shape:
                return
        self._component_shape = shape
        refresh_component_files()

    @override
    def tick(self) -> Generator[None, None, None]:
        """Recompute routes, compare to the previous tick, then delegate.

        A tick also swaps in the component boundary files, which no render stats.
        """
        parent_ticker = super().tick()
        while True:
            try:
                routes = self._collect_routes()
            except (OSError, ImportError, ValueError) as e:
                # Narrow on purpose, the watch helpers absorb what a router raises.
                logger.debug("next route set check skipped: %s", e)
                self._refresh_components(None)
            else:
                self._check_routes(routes)
                self._refresh_components(self._dir_signatures)
            yield next(parent_ticker)
