"""Discovery helpers that list page roots and component folder pairs.

These helpers power the development file watcher and the static
collector. They deliberately import `RouterFactory` lazily to avoid a
pages-urls circular import at module load.
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any

from next.conf import next_framework_settings


if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


def get_pages_directories_for_watch() -> list[Path]:
    """Return absolute page roots that the autoreloader should observe."""
    from next.urls import RouterFactory  # noqa: PLC0415

    configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(configs, list):
        return []
    seen: set[Path] = set()
    result: list[Path] = []
    for config in configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating backend for watch dirs from config %s", config
            )
            continue
        if not RouterFactory.is_filesystem_discovery_router(backend):
            continue
        fs_backend: Any = backend
        for p in itertools.chain(
            (p.resolve() for p in fs_backend._get_root_pages_paths()),
            (
                a.resolve()
                for app_name in fs_backend._get_installed_apps()
                if (a := fs_backend._get_app_pages_path(app_name))
            ),
        ):
            if p not in seen:
                seen.add(p.resolve())
                result.append(p.resolve())
    return result


def iter_pages_roots_with_components_folder_names() -> list[tuple[Path, str]]:
    """Return distinct page-root and components-folder-name pairs."""
    from next.urls import RouterFactory  # noqa: PLC0415

    configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(configs, list):
        return []
    seen: set[tuple[Path, str]] = set()
    result: list[tuple[Path, str]] = []
    for config in configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating backend for components watch globs from config %s",
                config,
            )
            continue
        if not RouterFactory.is_filesystem_discovery_router(backend):
            continue
        fs_backend: Any = backend
        comp_name = str(fs_backend._components_folder_name)
        for p in itertools.chain(
            (p.resolve() for p in fs_backend._get_root_pages_paths()),
            (
                a.resolve()
                for app_name in fs_backend._get_installed_apps()
                if (a := fs_backend._get_app_pages_path(app_name))
            ),
        ):
            key = (p, comp_name)
            if key not in seen:
                seen.add(key)
                result.append((p, comp_name))
    return result
