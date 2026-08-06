"""Read-only discovery of component filesystem paths for autoreload."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured

from next.backends import backend_entries, resolve_backend_class
from next.pages.watch import (
    components_folder_name_for_watch,
    iter_page_backends_for_watch,
    page_root_paths_for_watch,
)

from .backends import _DEFAULT_BACKEND_PATH, ComponentsBackend, FileComponentsBackend
from .info import _paths_from_component_info
from .loading import ModuleLoader
from .scanner import ComponentScanner, component_extra_roots_from_config


if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


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
    """Collect paths from component backend `DIRS` entries only."""
    result: set[Path] = set()
    for config in backend_entries("COMPONENT_BACKENDS"):
        try:
            klass = resolve_backend_class(
                config, base=ComponentsBackend, default=_DEFAULT_BACKEND_PATH
            )
        except (ImproperlyConfigured, ImportError):
            logger.exception(
                "error resolving component backend for autoreload scan %s", config
            )
            continue
        # A read-only scan reads roots off the config, so it skips the instance.
        if not issubclass(klass, FileComponentsBackend):
            continue
        scanner = ComponentScanner(module_loader=ModuleLoader())
        for root in component_extra_roots_from_config(config):
            try:
                for info in scanner.scan_directory(root, root, ""):
                    result |= _paths_from_component_info(info)
            except OSError as e:
                logger.debug("Cannot scan component root %s: %s", root, e)
    return result


def get_component_paths_for_watch() -> set[Path]:
    """Return filesystem paths that matter for the dev component reloader.

    The scan mutates neither the components manager nor the router registry.
    """
    page_paths = _collect_component_paths_under_page_trees()
    extra_paths = _collect_component_paths_from_backend_dirs()
    return page_paths | extra_paths


__all__ = ["get_component_paths_for_watch"]
