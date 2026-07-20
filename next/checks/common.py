"""Shared helpers used by per-subpackage system-check modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error

from next.conf.signals import settings_reloaded


if TYPE_CHECKING:
    from collections.abc import Iterator

    from next.components.manager import ComponentsManager
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


# One manager per `manage.py check` run instead of rescanning the page and
# component trees for every registered check.
_ROUTER_MANAGER_CACHE: dict[
    str,
    tuple[RouterManager | None, list[CheckMessage]] | None,
] = {"value": None}
_COMPONENTS_MANAGER_CACHE: dict[str, ComponentsManager | None] = {"value": None}

# Scan pairs materialised once per run and keyed by `id(router)`, which stays
# unique because the manager cache above keeps every router alive for the run
# and this slot is dropped before that manager is rebuilt.
_SCANNED_PAIRS_CACHE: dict[str, dict[int, list[tuple[str, Path]]]] = {"value": {}}


def get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Return a per-run cached `RouterManager` or initialisation errors."""
    cached = _ROUTER_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    # next.urls.checks imports next.checks.common, so the manager import is
    # deferred here to break the next.checks.common <-> next.urls cycle.
    from next.urls import RouterManager  # noqa: PLC0415

    result: tuple[RouterManager | None, list[CheckMessage]]
    try:
        router_manager = RouterManager()
        router_manager.reload()
    except (ImportError, AttributeError) as e:
        error = Error(
            f"Error initializing router manager: {e}",
            obj=settings,
            id="next.E007",
        )
        result = (None, [error])
    else:
        result = (router_manager, [])
    _ROUTER_MANAGER_CACHE["value"] = result
    return result


def reset_router_manager_cache(**_kwargs: object) -> None:
    """Drop the cached `RouterManager` and its scan pairs for the next run.

    Scan pairs are meaningless without the manager that kept their routers
    alive, so both slots clear on the same reset contour.
    """
    _ROUTER_MANAGER_CACHE["value"] = None
    _SCANNED_PAIRS_CACHE["value"] = {}


def get_components_manager() -> ComponentsManager:
    """Return a per-run cached `ComponentsManager` with its config loaded."""
    cached = _COMPONENTS_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    # next.components imports next.conf, which imports next.checks.common during
    # app setup, so the manager import is deferred here to break that cycle.
    from next.components.manager import ComponentsManager  # noqa: PLC0415

    manager = ComponentsManager()
    manager._reload_config()
    _COMPONENTS_MANAGER_CACHE["value"] = manager
    return manager


def reset_components_manager_cache(**_kwargs: object) -> None:
    """Drop the cached `ComponentsManager` so the next check run rebuilds it."""
    _COMPONENTS_MANAGER_CACHE["value"] = None


settings_reloaded.connect(reset_router_manager_cache)
settings_reloaded.connect(reset_components_manager_cache)


def get_first_root_pages_path(file_router: FileRouterBackend) -> Path | None:
    """Return the first entry from `_get_root_pages_paths` when defined."""
    if not hasattr(file_router, "_get_root_pages_paths"):
        return None
    root_paths = file_router._get_root_pages_paths()
    return root_paths[0] if root_paths else None


def get_first_app_pages_dir(file_router: FileRouterBackend) -> Path | None:
    """Return the first existing app pages directory, or `None`."""
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
    cache = _SCANNED_PAIRS_CACHE["value"]
    pairs = cache.get(id(router))
    if pairs is None:
        pairs = list(router._scan_pages_directory(pages_dir))
        cache[id(router)] = pairs
    yield from pairs


__all__ = [
    "errors_for_unknown_keys",
    "get_components_manager",
    "get_first_app_pages_dir",
    "get_first_root_pages_path",
    "get_pages_directory",
    "get_router_manager",
    "iter_scanned_page_pairs",
    "reset_components_manager_cache",
    "reset_router_manager_cache",
]
