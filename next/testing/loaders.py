"""Eager page-module loader used in tests.

`eager_load_pages` walks a pages directory and imports every `page.py`
file so that `@context` and `@forms.action` side effects register
before a test dispatches HTTP requests. Results are memoised per
absolute directory so repeated calls during a pytest session are
cheap.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_loaded_dirs: set[Path] = set()


def eager_load_pages(base_dir: Path | str) -> list[Path]:
    """Import every `page.py` under `base_dir` and return loaded paths.

    The call is idempotent for the same absolute directory. Non-existent
    paths raise `FileNotFoundError`. Importer errors bubble up so that
    broken page modules fail loudly in test setup rather than producing
    confusing 404 responses later.
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

    Intended for self-tests of the loader. Production test suites do
    not need to call this because each pytest session gets a fresh
    interpreter.
    """
    _loaded_dirs.clear()


__all__ = ["clear_loaded_dirs", "eager_load_pages"]
