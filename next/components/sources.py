"""The manager over every component source and the context keys it declares.

The live registry holds only what a request already made the router walk, so
the system checks and the JS-context key walk read a second manager built
here. Like `next.pages.scan` it stays under the coverage gate, because a
`checks` module is not the only caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.checks.common import get_router_manager, iter_page_tree_component_folders
from next.conf.signals import settings_reloaded

from .context import component
from .manager import ComponentsManager


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# One manager per `manage.py check` run instead of rescanning the component
# trees for every registered check.
_COMPONENTS_MANAGER_CACHE: dict[str, ComponentsManager | None] = {"value": None}


def get_components_manager() -> ComponentsManager:
    """Return a per-run cached `ComponentsManager` holding every component source.

    The manager is this module's own, because the live registry holds only what
    requests have already made the router walk. Dropped on `settings_reloaded`.
    """
    cached = _COMPONENTS_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    manager = ComponentsManager()
    # This module owns the manager, so no listener hears about its backends.
    manager.reload(notify=False)
    _COMPONENTS_MANAGER_CACHE["value"] = manager
    _register_page_tree_component_folders(manager)
    return manager


def _register_page_tree_component_folders(manager: ComponentsManager) -> None:
    """Register every components folder the configured page trees carry.

    The folders and the registration are the router's own, so a reader sees
    the store a render would resolve against.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    for router in router_manager.backends:
        for folder, tree_root, route_trail in iter_page_tree_component_folders(router):
            manager.register_router_walk_folder(folder, tree_root, route_trail)


def reset_components_manager_cache(**kwargs) -> None:
    """Drop the cached `ComponentsManager` so the next read rebuilds it."""
    _COMPONENTS_MANAGER_CACHE["value"] = None


settings_reloaded.connect(reset_components_manager_cache)


def iter_serialized_component_context_keys() -> Iterator[tuple[Path, str]]:
    """Yield the `component.py` path and key of every keyed `serialize=True` context.

    A keyless `serialize=True` callable spreads the keys of the dict it
    returns at render time, so those keys exist only at runtime and never
    travel through here. Reading the keys imports every `component.py`, since
    the decorator state is the truth, so a check calling this pays that import
    even under `LAZY_COMPONENT_MODULES`.
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
