"""Shared helpers used by per-subpackage system-check modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error


if TYPE_CHECKING:
    from collections.abc import Iterator

    from next.urls import FileRouterBackend, RouterBackend, RouterManager


def errors_for_unknown_keys(
    config: dict[str, Any],
    *,
    allowed: frozenset[str],
    prefix: str,
) -> list[CheckMessage]:
    """Return an `Error` list when `config` contains keys outside `allowed`."""
    unknown = sorted(k for k in config if k not in allowed)
    if not unknown:
        return []
    unknown_fmt = ", ".join(repr(k) for k in unknown)
    allowed_fmt = ", ".join(sorted(allowed))
    return [
        Error(
            f"{prefix} has unknown keys {unknown_fmt}. Allowed keys are {allowed_fmt}.",
            obj=settings,
            id="next.E035",
        ),
    ]


def get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Return a fresh `RouterManager` or initialisation errors."""
    from next.urls import RouterManager  # noqa: PLC0415

    try:
        router_manager = RouterManager()
        router_manager._reload_config()
    except (ImportError, AttributeError) as e:
        error = Error(
            f"Error initializing router manager: {e}",
            obj=settings,
            id="next.E007",
        )
        return None, [error]
    else:
        return router_manager, []


def get_first_root_pages_path(file_router: FileRouterBackend) -> Path | None:
    """Return the first entry from `_get_root_pages_paths` when defined."""
    if not hasattr(file_router, "_get_root_pages_paths"):
        return None
    root_paths = file_router._get_root_pages_paths()
    return root_paths[0] if root_paths else None


def get_first_app_pages_dir(file_router: FileRouterBackend) -> Path | None:
    """Return the first existing app pages directory, or `None`."""
    from pathlib import Path  # noqa: PLC0415

    for app_config in apps.get_app_configs():
        potential = Path(app_config.path) / str(file_router.pages_dir)
        if potential.exists():
            return potential
    return None


def get_pages_directory(router: RouterBackend) -> Path | None:
    """Return one representative pages root directory for scanning checks."""
    if not hasattr(router, "pages_dir"):
        return None
    file_router = cast("FileRouterBackend", router)
    if file_router.app_dirs:
        return get_first_app_pages_dir(file_router) or get_first_root_pages_path(
            file_router,
        )
    p = Path(str(file_router.pages_dir))
    return get_first_root_pages_path(file_router) or (p if p.exists() else None)


def iter_scanned_page_pairs(
    router: RouterBackend,
) -> Iterator[tuple[str, Path]]:
    """Yield pairs from `_scan_pages_directory` when the router is scannable."""
    if not hasattr(router, "_scan_pages_directory"):
        return
    pages_dir = get_pages_directory(router)
    if not pages_dir:
        return
    yield from router._scan_pages_directory(pages_dir)


__all__ = [
    "errors_for_unknown_keys",
    "get_first_app_pages_dir",
    "get_first_root_pages_path",
    "get_pages_directory",
    "get_router_manager",
    "iter_scanned_page_pairs",
]
