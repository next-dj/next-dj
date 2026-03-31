"""Webserver helpers and the development autoreload API.

NextStatReloader extends Django StatReloader with an extra set comparison for
discovered routes only. ``.djx`` files are not watched: they are read at render time
with mtime-based invalidation (see pages and components). The same module lists
watch roots and globs and exposes a small API for tooling that needs stable
filesystem roots.
"""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterable  # noqa: TC003
from pathlib import Path  # noqa: TC003
from typing import Protocol, runtime_checkable

from django.utils.autoreload import StatReloader

from .components import component_extra_roots_from_config
from .conf import next_framework_settings
from .pages import (
    get_pages_directories_for_watch,
    iter_pages_roots_with_components_folder_names,
)
from .urls import scan_pages_tree


logger = logging.getLogger(__name__)


@runtime_checkable
class FilesystemWatchContributor(Protocol):
    """Yield (root, glob) pairs for ``StatReloader.watch_dir``."""

    def iter_watch_specs(self) -> Iterable[tuple[Path, str]]:
        """Each item is a root path and a glob pattern relative to that root."""
        ...


_registered_extra_watch_specs: list[tuple[Path, str]] = []


def register_autoreload_watch_spec(path: Path, glob: str) -> None:
    """Register one extra directory and glob pair for the development file watcher.

    Built-in globs already come from NEXT_FRAMEWORK. Call this from your own
    AppConfig.ready if you need more trees watched without changing the next package.
    """
    _registered_extra_watch_specs.append((path, glob))


def _dedupe_watch_specs(
    specs: Iterable[tuple[Path, str]],
) -> list[tuple[Path, str]]:
    seen: set[tuple[Path, str]] = set()
    out: list[tuple[Path, str]] = []
    for path, glob in specs:
        try:
            key = (path.resolve(), glob)
        except OSError:
            key = (path, glob)
        if key not in seen:
            seen.add(key)
            out.append((path, glob))
    return out


def iter_default_autoreload_watch_specs() -> list[tuple[Path, str]]:
    """Return the default watch specs for pages trees and filesystem components.

    ``.djx`` is intentionally omitted. Template edits do not restart the process, but
    Python entrypoints (``page.py``, ``component.py``) trigger reload when their
    mtimes change.
    """
    specs: list[tuple[Path, str]] = [
        (p, "**/page.py") for p in get_pages_directories_for_watch()
    ]
    specs.extend(
        (root, f"**/{comp_name}/**/component.py")
        for root, comp_name in iter_pages_roots_with_components_folder_names()
    )
    comp_cfgs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if isinstance(comp_cfgs, list):
        for config in comp_cfgs:
            if not isinstance(config, dict):
                continue
            specs.extend(
                (root, "**/component.py")
                for root in component_extra_roots_from_config(config)
            )
    return specs


def iter_all_autoreload_watch_specs() -> list[tuple[Path, str]]:
    """Return default watch specs plus registered extras, with duplicates removed."""
    return _dedupe_watch_specs(
        (*iter_default_autoreload_watch_specs(), *_registered_extra_watch_specs)
    )


def get_framework_filesystem_roots_for_linking() -> list[Path]:
    """Return sorted unique roots from page trees and component ``DIRS``."""
    roots: set[Path] = {p.resolve() for p in get_pages_directories_for_watch()}
    comp_cfgs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if isinstance(comp_cfgs, list):
        for config in comp_cfgs:
            if isinstance(config, dict):
                roots.update(
                    p.resolve() for p in component_extra_roots_from_config(config)
                )
    return sorted(roots)


class NextStatReloader(StatReloader):
    """Reload when the discovered route set changes, not when ``.djx`` files change."""

    def __init__(self) -> None:
        """Initialize cached route set for comparison between ticks."""
        super().__init__()
        self._previous_routes: set[tuple[str, Path]] | None = None

    def _check_routes(self, current: set[tuple[str, Path]]) -> None:
        """Notify the reloader if the discovered route set changed."""
        prev = self._previous_routes
        if prev is None or current == prev:
            self._previous_routes = current
            return
        diff = (current - prev) or (prev - current)
        if diff:
            self.notify_file_changed(next(iter(diff))[1])
        self._previous_routes = current

    def tick(self) -> Generator[None, None, None]:
        """Recompute routes and compare to the previous tick, then parent ``tick``."""
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
