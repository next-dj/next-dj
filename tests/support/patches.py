from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from tests.support.helpers import next_framework_settings_for_checks


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable
    from pathlib import Path


@contextmanager
def patch_checks_router_manager(
    *,
    pages_directory: Path,
    scan_routes: Iterable[tuple[str, Path]],
) -> Generator[tuple[MagicMock, MagicMock, MagicMock], None, None]:
    """Patch `get_router_manager` and `get_pages_directory` for page checks tests."""
    routes = list(scan_routes)
    mock_mgr = MagicMock()
    mock_router = MagicMock()
    mock_mgr._backends = [mock_router]
    mock_router.pages_dir = "pages"
    mock_router.app_dirs = True
    mock_router._scan_pages_directory.return_value = routes
    with (
        patch(
            "next.pages.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
        patch(
            "next.urls.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
        patch(
            "next.checks.common.get_pages_directory",
            return_value=pages_directory,
        ) as mock_get_pages_dir,
    ):
        yield mock_mgr, mock_router, mock_get_pages_dir


@contextmanager
def patch_checks_router_manager_with_routers(
    *,
    routers: list[object],
) -> Generator[MagicMock, None, None]:
    """Patch `get_router_manager` so the manager exposes the given routers list."""
    mock_mgr = MagicMock()
    mock_mgr._backends = list(routers)
    with (
        patch(
            "next.pages.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
        patch(
            "next.urls.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
    ):
        yield mock_mgr


@contextmanager
def patch_checks_components_manager(
    *fake_backends: object,
) -> Generator[MagicMock, None, None]:
    """Patch components-check settings and `ComponentsManager` with fake backends."""
    mock_ns = next_framework_settings_for_checks(
        backends=[
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_components",
            },
        ],
    )
    with (
        patch("next.components.checks.next_framework_settings", mock_ns),
        patch("next.components.checks.ComponentsManager") as mock_manager_klass,
    ):
        mock_manager = mock_manager_klass.return_value
        mock_manager._reload_config = lambda: None
        mock_manager._backends = list(fake_backends)
        yield mock_manager
