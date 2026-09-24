"""Eager page-module loader used in tests.

`eager_load_pages` imports every `page.py` under a directory so `@context` and
`@forms.action` side effects register before a test dispatches HTTP requests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from next.components import components_manager
from next.discovery import get_router_manager, iter_page_tree_component_folders


_loaded_dirs: set[Path] = set()


def eager_load_pages(base_dir: Path | str) -> list[Path]:
    """Import every `page.py` under `base_dir` and return loaded paths.

    The call is idempotent per absolute directory, and an importer error bubbles up so a
    broken page fails in setup rather than as a confusing 404 later.
    """
    directory = Path(base_dir).resolve()
    if not directory.is_dir():
        msg = f"Pages directory not found: {directory}"
        raise FileNotFoundError(msg)
    if directory in _loaded_dirs:
        return []
    loaded: list[Path] = []
    for page_file in sorted(directory.rglob("page.py")):
        _load_module_from_path(page_file)
        loaded.append(page_file)
    _loaded_dirs.add(directory)
    return loaded


def _load_module_from_path(path: Path) -> None:
    module_name = _derive_module_name(path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        msg = f"Cannot build import spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


def _derive_module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    cleaned = [p.replace("[", "_").replace("]", "_").replace(":", "_") for p in parts]
    return "next_testing_pages." + "_".join(cleaned[-6:])


def clear_loaded_dirs() -> None:
    """Drop the memoisation cache so the next call reloads page modules.

    Intended for self-tests of the loader. Production test suites do not need to call
    this because each pytest session gets a fresh interpreter.
    """
    _loaded_dirs.clear()


def _register_page_tree_components() -> None:
    """Register the components folder of every configured page tree.

    A live registry holds only what a request made the router walk, and a component
    test that renders before the first request would miss the folders of the trees.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    for router in router_manager.backends:
        for folder, tree_root, route_trail in iter_page_tree_component_folders(router):
            components_manager.register_router_walk_folder(
                folder, tree_root, route_trail
            )


def eager_load_components() -> None:
    """Import every registered `component.py` so decorators register before tests."""
    _register_page_tree_components()
    for backend in components_manager.backends:
        backend.discover()
        # Running the module top level is its own capability, not `discover`'s.
        backend.import_component_modules()


__all__ = ["clear_loaded_dirs", "eager_load_components", "eager_load_pages"]
