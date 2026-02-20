"""Utilities for next-dj."""

import logging
import time
from collections.abc import Generator
from pathlib import Path  # noqa: TC003

from django.utils.autoreload import StatReloader

from .pages import (
    get_layout_djx_paths_for_watch,
    get_template_djx_paths_for_watch,
)
from .urls import _scan_pages_directory, get_pages_directories_for_watch


logger = logging.getLogger(__name__)


class NextStatReloader(StatReloader):
    """StatReloader that triggers reload when pages/layouts/templates set changes.

    Reload on add or remove: page.py/virtual pages, layout.djx, template.djx
    so that urlpatterns and render hierarchy are recalculated.
    """

    def _notify_if_set_changed(self, previous: set | None, current: set) -> None:
        """If current != previous, call notify_file_changed with one path from diff."""
        if previous is None or current == previous:
            return
        diff = (current - previous) or (previous - current)
        if diff:
            item = next(iter(diff))
            path = item[1] if isinstance(item, tuple) else item
            self.notify_file_changed(path)

    def tick(self) -> Generator[None, None, None]:
        """Parent mtime check and route/layout/template set change check, then yield."""
        mtimes: dict = {}
        previous_route_set: set[tuple[str, Path]] | None = None
        previous_layout_set: set[Path] | None = None
        previous_template_set: set[Path] | None = None

        while True:
            try:
                current_route_set = {
                    (url_path, file_path.resolve())
                    for pages_path in get_pages_directories_for_watch()
                    for url_path, file_path in _scan_pages_directory(pages_path)
                }
                current_layout_set = get_layout_djx_paths_for_watch()
                current_template_set = get_template_djx_paths_for_watch()

                self._notify_if_set_changed(previous_route_set, current_route_set)
                self._notify_if_set_changed(previous_layout_set, current_layout_set)
                self._notify_if_set_changed(previous_template_set, current_template_set)

                previous_route_set = current_route_set
                previous_layout_set = current_layout_set
                previous_template_set = current_template_set
            except (OSError, ImportError, ValueError) as e:
                logger.debug("next route/layout/template set check skipped: %s", e)

            # Parent logic: mtime check for all watched files
            for filepath, mtime in self.snapshot_files():
                old_time = mtimes.get(filepath)
                mtimes[filepath] = mtime
                if old_time is None:
                    continue
                if mtime > old_time:
                    self.notify_file_changed(filepath)

            time.sleep(self.SLEEP_TIME)
            yield
