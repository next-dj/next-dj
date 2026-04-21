"""Read-only discovery of component filesystem paths for autoreload.

`get_component_paths_for_watch` combines page-tree and backend `DIRS`
scans so the dev reloader can register component files without
mutating the manager state.
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any

from next.conf import next_framework_settings

from .backends import ComponentsFactory, FileComponentsBackend
from .info import _paths_from_component_info
from .loading import ModuleLoader
from .scanner import ComponentScanner, component_extra_roots_from_config


if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


def _collect_paths_for_one_pages_root(
    scanner: ComponentScanner,
    comp_name: str,
    root: Path,
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
        logger.debug(
            "Cannot scan %s for component dirs %s: %s",
            root,
            comp_name,
            e,
        )
    return result


def _collect_component_paths_under_page_trees() -> set[Path]:
    """Collect component paths from page backends without mutating registries."""
    from next.urls import RouterFactory  # noqa: PLC0415

    result: set[Path] = set()
    page_configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(page_configs, list):
        return result
    for config in page_configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating page backend for component autoreload scan %s",
                config,
            )
            continue
        if not RouterFactory.is_filesystem_discovery_router(backend):
            continue
        fs_backend: Any = backend
        comp_name = str(fs_backend._components_folder_name)
        scanner = ComponentScanner(comp_name)
        for root in itertools.chain(
            (p.resolve() for p in fs_backend._get_root_pages_paths()),
            (
                a.resolve()
                for app_name in fs_backend._get_installed_apps()
                if (a := fs_backend._get_app_pages_path(app_name))
            ),
        ):
            result |= _collect_paths_for_one_pages_root(scanner, comp_name, root)
    return result


def _collect_component_paths_from_backend_dirs() -> set[Path]:
    """Collect paths from component backend `DIRS` entries only."""
    result: set[Path] = set()
    comp_configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(comp_configs, list):
        return result
    for config in comp_configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = ComponentsFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating component backend for autoreload scan %s", config
            )
            continue
        if not isinstance(backend, FileComponentsBackend):
            continue
        scanner = ComponentScanner(
            backend.components_dir,
            module_loader=ModuleLoader(),
        )
        for root in component_extra_roots_from_config(config):
            try:
                for info in scanner.scan_directory(root, root, ""):
                    result |= _paths_from_component_info(info)
            except OSError as e:
                logger.debug(
                    "Cannot scan component root %s: %s",
                    root,
                    e,
                )
    return result


def get_component_paths_for_watch() -> set[Path]:
    """Return filesystem paths that matter for the dev component reloader.

    This performs a read-only scan. It does not mutate the components manager
    or router registration state.
    """
    page_paths = _collect_component_paths_under_page_trees()
    extra_paths = _collect_component_paths_from_backend_dirs()
    return page_paths | extra_paths


__all__ = ["get_component_paths_for_watch"]
