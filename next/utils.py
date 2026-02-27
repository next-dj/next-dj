"""Store utils for next-dj framework."""

import logging
from collections.abc import Generator
from pathlib import Path

from django.utils.autoreload import StatReloader

from .pages import (
    get_layout_djx_paths_for_watch,
    get_pages_directories_for_watch,
    get_template_djx_paths_for_watch,
)
from .urls import _scan_pages_directory


logger = logging.getLogger(__name__)


class NextStatReloader(StatReloader):
    """StatReloader that also reacts to route/layout/template set changes."""

    def __init__(self) -> None:
        """Init previous-route/layout/template state for set comparison."""
        super().__init__()
        self._previous_routes: set[tuple[str, Path]] | None = None
        self._previous_layouts: set[Path] | None = None
        self._previous_templates: set[Path] | None = None

    def _check_routes(self, current: set[tuple[str, Path]]) -> None:
        """Notify once when the set of (url_path, file_path) routes changes."""
        prev = self._previous_routes
        if prev is None or current == prev:
            self._previous_routes = current
            return
        diff = (current - prev) or (prev - current)
        if diff:
            self.notify_file_changed(next(iter(diff))[1])
        self._previous_routes = current

    def _check_layouts(
        self, current: set[Path], current_routes: set[tuple[str, Path]]
    ) -> None:
        """Notify when the set of layout files changes."""
        prev = self._previous_layouts
        if prev is None or current == prev:
            self._previous_layouts = current
            return
        layout_diff = (current - prev) or (prev - current)
        for p in layout_diff:
            layout_dir = p.parent
            for _, file_path in current_routes:
                try:
                    if file_path.is_relative_to(layout_dir):
                        self.notify_file_changed(p)
                        self._previous_layouts = current
                        return
                except ValueError:
                    continue
        self._previous_layouts = current

    def _check_templates(self, current: set[Path]) -> None:
        """Notify once when the set of template files changes."""
        prev = self._previous_templates
        if prev is None or current == prev:
            self._previous_templates = current
            return
        diff = (current - prev) or (prev - current)
        if diff:
            self.notify_file_changed(next(iter(diff)))
        self._previous_templates = current

    def tick(self) -> Generator[None, None, None]:
        """Run next-dj set checks, then one parent tick step."""
        parent_ticker = super().tick()
        while True:
            try:
                routes = {
                    (url_path, file_path.resolve())
                    for pages_path in get_pages_directories_for_watch()
                    for url_path, file_path in _scan_pages_directory(pages_path)
                }
                layouts = get_layout_djx_paths_for_watch()
                templates = get_template_djx_paths_for_watch()
            except (OSError, ImportError, ValueError) as e:
                logger.debug("next route/layout/template set check skipped: %s", e)
            else:
                self._check_routes(routes)
                self._check_layouts(layouts, routes)
                self._check_templates(templates)
            yield next(parent_ticker)
