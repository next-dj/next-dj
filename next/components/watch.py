"""Read-only discovery of component filesystem paths for autoreload."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from next.conf.signals import settings_reloaded
from next.pages.watch import (
    components_folder_name_for_watch,
    iter_page_backends_for_watch,
    page_root_paths_for_watch,
)

from .info import _paths_from_component_info
from .loading import ModuleLoader
from .manager import components_manager
from .scanner import ComponentScanner


if TYPE_CHECKING:
    from .backends import ComponentsBackend


logger = logging.getLogger(__name__)

_FAILED_ROOTS = (
    "%s failed to report the trees it watches, so it contributes none of them. "
    "The same failure is not logged again until the framework is reconfigured."
)

_MALFORMED_ROOTS = (
    "%s reported a watched tree that is no path, so it contributes none of them. "
    "The same failure is not logged again until the framework is reconfigured."
)

# A backend that keeps raising is read once a second by the reloader and once per
# static lookup, so the same failure is reported once per configuration.
_reported_failures: set[str] = set()


def _forget_reported_failures(**kwargs) -> None:
    """Re-arm the diagnostics of the backends that failed their watch read."""
    _reported_failures.clear()


settings_reloaded.connect(_forget_reported_failures)


def _first_failure(source: str) -> bool:
    """Whether this failure is unreported, recording it when it is."""
    if source in _reported_failures:
        return False
    _reported_failures.add(source)
    return True


def _roots_of(backend: ComponentsBackend) -> list[Path]:
    """Return the trees one backend reports watching, or none when it cannot.

    Every other read of third-party backend code in the watch layer answers an
    empty result rather than reaching a caller that dereferences what came
    back, and a static lookup runs on this path too.
    """
    source = type(backend).__name__
    try:
        reported = list(backend.watch_roots())
    except Exception:
        if _first_failure(source):
            logger.exception(_FAILED_ROOTS, source)
        return []
    if any(not isinstance(root, Path) for root in reported):
        if _first_failure(source):
            logger.error(_MALFORMED_ROOTS, source)
        return []
    return reported


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
    for backend in iter_page_backends_for_watch():
        comp_name = components_folder_name_for_watch(backend)
        if comp_name is None:
            continue
        scanner = ComponentScanner()
        for root in page_root_paths_for_watch(backend):
            result |= _collect_paths_for_one_pages_root(scanner, comp_name, root)
    return result


def _collect_component_paths_from_backend_dirs() -> set[Path]:
    """Collect paths from the trees each components backend reports watching."""
    result: set[Path] = set()
    for root in component_watch_roots():
        scanner = ComponentScanner(module_loader=ModuleLoader())
        try:
            for info in scanner.scan_directory(root, root, ""):
                result |= _paths_from_component_info(info)
        except OSError as e:
            logger.debug("Cannot scan component root %s: %s", root, e)
    return result


def get_component_paths_for_watch() -> set[Path]:
    """Return filesystem paths that matter for the dev component reloader.

    The scan registers nothing. It asks the loaded backends which trees they
    watch and walks those with a scanner of its own, so neither the component
    registries nor the router registry move.
    """
    page_paths = _collect_component_paths_under_page_trees()
    extra_paths = _collect_component_paths_from_backend_dirs()
    return page_paths | extra_paths


__all__ = ["component_watch_roots", "get_component_paths_for_watch"]
