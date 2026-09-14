"""The manager over every component source and the context keys it declares.

The live registry holds only what a request made the router walk, so the checks and the
JS-context key walk read a second manager built here, outside any `checks` module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.conf.signals import settings_reloaded
from next.discovery import get_router_manager, iter_page_tree_component_folders

from .context import component
from .manager import ComponentsManager


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _ComponentsManagerCache:
    """The one manager a check run builds, instead of one per asking check."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.held: ComponentsManager | None = None


_components_manager_cache = _ComponentsManagerCache()


def get_components_manager() -> ComponentsManager:
    """Return a per-run cached `ComponentsManager` holding every component source.

    The manager is this module's own, because the live registry holds only what
    requests have already made the router walk. Dropped on `settings_reloaded`.
    """
    cached = _components_manager_cache.held
    if cached is not None:
        return cached
    manager = ComponentsManager()
    # This module owns the manager, so no listener hears about its backends.
    manager.reload(notify=False)
    _components_manager_cache.held = manager
    _register_page_tree_component_folders(manager)
    return manager


def _register_page_tree_component_folders(manager: ComponentsManager) -> None:
    """Register every components folder the configured page trees carry.

    The router's own folders and registration make a reader see what a render sees.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    for router in router_manager.backends:
        for folder, tree_root, route_trail in iter_page_tree_component_folders(router):
            manager.register_router_walk_folder(folder, tree_root, route_trail)


def reset_components_manager_cache(**kwargs) -> None:
    """Drop the cached `ComponentsManager` so the next read rebuilds it."""
    _components_manager_cache.held = None


settings_reloaded.connect(reset_components_manager_cache)


def iter_serialized_component_context_keys() -> Iterator[tuple[Path, str]]:
    """Yield the `component.py` path and key of every keyed `serialize=True` context.

    Reading the keys imports every `component.py`, even under `LAZY_COMPONENT_MODULES`.
    The keys of a keyless callable exist only at render time.
    """
    manager = get_components_manager()
    for backend in manager.backends:
        for module_path in backend.import_component_modules():
            for entry in component.get_functions(module_path):
                if entry.serialize and entry.key is not None:
                    yield module_path, entry.key


__all__ = [
    "get_components_manager",
    "iter_serialized_component_context_keys",
    "reset_components_manager_cache",
]
