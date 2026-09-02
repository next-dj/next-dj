from __future__ import annotations

import copy
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from next.static import default_kinds, default_placeholders
from next.static.discovery import default_stems
from tests.support.helpers import next_framework_settings_for_checks


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


# The highest `_version` each registry has carried, so a restore can roll the
# state back without rolling the counter every asset plan compares back with it.
_REGISTRY_VERSION_HIGH_WATER: dict[int, int] = {}


@contextmanager
def restored_static_registries() -> Generator[None, None, None]:
    """Put the stem, kind, and slot registries back the way the body found them.

    All three are process globals whose generation every asset plan compares
    against, so a test teaching the framework a new shape puts them back. The
    state goes back but `_version` only moves forward, because two registry
    states sharing a generation would make a genuinely stale plan read fresh.
    """
    registries = (default_stems, default_kinds, default_placeholders)
    saved = [copy.deepcopy(registry.__dict__) for registry in registries]
    try:
        yield
    finally:
        for registry, state in zip(registries, saved, strict=True):
            key = id(registry)
            reached = max(
                _REGISTRY_VERSION_HIGH_WATER.get(key, 0),
                registry.version,
                state["_version"],
            )
            registry.__dict__.clear()
            registry.__dict__.update(state)
            registry._version = reached + 1
            _REGISTRY_VERSION_HIGH_WATER[key] = reached + 1


@contextmanager
def importable_dir(directory: Path) -> Generator[None, None, None]:
    """Put `directory` on `sys.path` and drop the modules imported from it."""
    before = set(sys.modules)
    sys.path.insert(0, str(directory))
    try:
        yield
    finally:
        sys.path.remove(str(directory))
        for name in set(sys.modules) - before:
            del sys.modules[name]


@contextmanager
def patch_checks_router_manager(
    *, pages_directory: Path
) -> Generator[tuple[MagicMock, MagicMock, MagicMock], None, None]:
    """Point the check seams at one real pages directory through a stub manager."""
    mock_mgr = MagicMock()
    mock_router = MagicMock()
    mock_mgr.backends = (mock_router,)
    mock_router.components_folder_name.return_value = None
    with (
        patch("next.pages.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch("next.urls.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch(
            "next.checks.common.get_pages_directories", return_value=[pages_directory]
        ) as mock_get_pages_dirs,
    ):
        yield mock_mgr, mock_router, mock_get_pages_dirs


@contextmanager
def patch_checks_router_manager_with_routers(
    *, routers: list[object]
) -> Generator[MagicMock, None, None]:
    """Patch `get_router_manager` so the manager exposes the given routers list."""
    mock_mgr = MagicMock()
    mock_mgr.backends = tuple(routers)
    with (
        patch("next.pages.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch("next.partial.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch("next.urls.checks.get_router_manager", return_value=(mock_mgr, [])),
    ):
        yield mock_mgr


@contextmanager
def patch_checks_components_manager(*fake_backends) -> Generator[MagicMock, None, None]:
    """Patch components-check settings and `ComponentsManager` with fake backends."""
    mock_ns = next_framework_settings_for_checks(
        backends=[
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_components",
            }
        ]
    )
    mock_manager = MagicMock()
    mock_manager.backends = tuple(fake_backends)
    with (
        patch("next.components.checks.next_framework_settings", mock_ns),
        patch(
            "next.components.checks.get_components_manager", return_value=mock_manager
        ),
    ):
        yield mock_manager
