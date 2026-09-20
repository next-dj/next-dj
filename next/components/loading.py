"""LRU cache and loader for `component.py` modules.

The cache spares repeated renders the cost of re-executing module top level.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import TYPE_CHECKING, cast

from next.caches import LruCache


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


logger = logging.getLogger(__name__)

_CACHE_MISS = object()

_LAST_LOAD_ERROR: dict[Path, Exception] = {}


def last_load_error(file_path: Path) -> Exception | None:
    """Return what the last import of `file_path` raised, while one stands.

    A failed import degrades the render to the bare template, so a check names it.
    """
    return _LAST_LOAD_ERROR.get(file_path)


class ModuleCache:
    """Remembers loaded Python modules by file path and drops the oldest when full."""

    def __init__(self, maxsize: int = 128) -> None:
        """Create an LRU cache with the given capacity."""
        self._order: LruCache[Path, ModuleType | None] = LruCache(maxsize)

    def get(self, path: Path) -> ModuleType | object | None:
        """Return the cached module, a cached `None`, or the miss sentinel."""
        try:
            return self._order[path]
        except KeyError:
            return _CACHE_MISS

    def set(self, path: Path, module: ModuleType | None) -> None:
        """Store the module (or `None` on failure) under `path`, evicting when full."""
        self._order[path] = module

    def clear(self) -> None:
        """Drop every entry from the cache."""
        self._order.clear()

    def __len__(self) -> int:
        """Return the number of cached entries.

        An empty cache is therefore falsy, so a caller taking one as an
        argument checks it against `None` rather than for truth.
        """
        return len(self._order)

    def __contains__(self, path: Path) -> bool:
        """Return True when `path` currently has a cached entry."""
        return path in self._order


class ModuleLoader:
    """Loads a `.py` file as a module and reuses the last load for the same path."""

    def __init__(self, cache: ModuleCache | None = None) -> None:
        """Bind the loader to a shared or new `ModuleCache`."""
        self._cache = cache if cache is not None else ModuleCache()

    def load(self, path: Path) -> ModuleType | None:
        """Return the module for `path`, loading it on cache miss."""
        cached = self._cache.get(path)
        if cached is _CACHE_MISS:
            module = self._load_from_disk(path)
            self._cache.set(path, module)
            return module
        return cast("ModuleType | None", cached)

    def _load_from_disk(self, path: Path) -> ModuleType | None:
        try:
            spec = importlib.util.spec_from_file_location(
                f"component_module_{path.stem}", path
            )
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except (ImportError, AttributeError, OSError, SyntaxError) as e:
            logger.debug("Could not load module %s: %s", path, e)
            _LAST_LOAD_ERROR[path] = e
            return None
        else:
            _LAST_LOAD_ERROR.pop(path, None)
            return module


__all__ = ["ModuleCache", "ModuleLoader", "last_load_error"]
