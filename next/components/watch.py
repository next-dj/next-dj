"""Read-only discovery of component filesystem paths for autoreload."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from next.diagnostics import BackendReadLog
from next.pages.watch import (
    components_folder_name_for_watch,
    iter_page_backends_for_watch,
    page_root_paths_for_watch,
)

from .info import _paths_from_component_info
from .manager import components_manager
from .scanner import ComponentScanner


if TYPE_CHECKING:
    from .backends import ComponentsBackend


logger = logging.getLogger(__name__)

# Every read of a components backend goes through one log, so a backend that keeps
# raising reports once per configuration wherever the watch layer reads it.
_reads = BackendReadLog(logger)


def _roots_of(backend: ComponentsBackend) -> list[Path]:
    """Return the trees one backend reports watching, or none when it cannot.

    Every third-party backend read in the watch layer answers an empty result rather
    than reaching a caller that dereferences what came back.
    """
    return _reads.read(
        backend,
        "watched trees",
        lambda: list(backend.watch_roots()),
        valid=lambda roots: all(isinstance(root, Path) for root in roots),
        default=[],
    )


def component_watch_roots() -> list[Path]:
    """Return every tree the loaded components backends report watching.

    The one reader of `watch_roots`, so the autoreload watcher, the link
    tooling and the staticfiles finder all see a backend fail the same way.
    """
    return [
        root for backend in components_manager.backends for root in _roots_of(backend)
    ]


def _collect_paths_for_one_pages_root(
    scanner: ComponentScanner, comp_name: str, root: Path
) -> set[Path]:
    """Gather component paths under one pages tree root."""
    result: set[Path] = set()
    try:
        for path in root.glob(f"**/{comp_name}"):
            if not path.is_dir():
                continue
            try:
                rel_parent = path.parent.relative_to(root)
            except ValueError:
                continue
            scope_relative = "/".join(rel_parent.parts) if rel_parent.parts else ""
            for info in scanner.scan_directory(path, root, scope_relative):
                result |= _paths_from_component_info(info)
    except OSError as e:
        logger.debug("Cannot scan %s for component dirs %s: %s", root, comp_name, e)
    return result


def _collect_component_paths_under_page_trees() -> set[Path]:
    """Collect component paths from page backends without mutating registries."""
    result: set[Path] = set()
    # One scanner for the whole read, so a module two trees reach is loaded once.
    scanner = ComponentScanner()
    for backend in iter_page_backends_for_watch():
        comp_name = components_folder_name_for_watch(backend)
        if comp_name is None:
            continue
        for root in page_root_paths_for_watch(backend):
            result |= _collect_paths_for_one_pages_root(scanner, comp_name, root)
    return result


def _collect_component_paths_from_backend_dirs() -> set[Path]:
    """Collect paths from the trees each components backend reports watching."""
    result: set[Path] = set()
    # One scanner for the whole read, so a module two trees reach is loaded once.
    scanner = ComponentScanner()
    for root in component_watch_roots():
        try:
            for info in scanner.scan_directory(root, root, ""):
                result |= _paths_from_component_info(info)
        except OSError as e:
            logger.debug("Cannot scan component root %s: %s", root, e)
    return result


def get_component_paths_for_watch() -> set[Path]:
    """Return filesystem paths that matter for the dev component reloader.

    A scanner of its own keeps both the component and the router registries still.
    """
    page_paths = _collect_component_paths_under_page_trees()
    extra_paths = _collect_component_paths_from_backend_dirs()
    return page_paths | extra_paths


__all__ = ["component_watch_roots", "get_component_paths_for_watch"]
