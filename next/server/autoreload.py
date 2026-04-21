"""Custom `StatReloader` that also watches the discovered route set.

`NextStatReloader` subclasses Django's `StatReloader` and adds a route
set comparison between ticks. When pages appear or disappear from the
routing tree, the reloader notifies Django even if no mtime changed.
`.djx` templates are deliberately not watched. They are re-read on
render with mtime-based invalidation inside pages and components.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils.autoreload import StatReloader

from next.pages.watch import get_pages_directories_for_watch
from next.urls.dispatcher import scan_pages_tree


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


logger = logging.getLogger(__name__)


class NextStatReloader(StatReloader):
    """Reload on route set changes in addition to `.py` mtime changes."""

    def __init__(self) -> None:
        """Initialise the cached route set used for tick-to-tick diffs."""
        super().__init__()
        self._previous_routes: set[tuple[str, Path]] | None = None

    def _check_routes(self, current: set[tuple[str, Path]]) -> None:
        """Notify the reloader when the discovered route set changed."""
        prev = self._previous_routes
        if prev is None or current == prev:
            self._previous_routes = current
            return
        diff = (current - prev) or (prev - current)
        if diff:
            self.notify_file_changed(next(iter(diff))[1])
        self._previous_routes = current

    def tick(self) -> Generator[None, None, None]:
        """Recompute routes, compare to the previous tick, then delegate."""
        parent_ticker = super().tick()
        while True:
            try:
                routes = {
                    (url_path, file_path.resolve())
                    for pages_path in get_pages_directories_for_watch()
                    for url_path, file_path in scan_pages_tree(pages_path)
                }
            except (OSError, ImportError, ValueError) as e:
                logger.debug("next route set check skipped: %s", e)
            else:
                self._check_routes(routes)
            yield next(parent_ticker)
