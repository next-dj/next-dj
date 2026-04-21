"""LRU cache and loader for `component.py` modules.

`ModuleCache` keeps a bounded mapping of file paths to loaded modules
so repeated renders do not re-execute module top level on every
request. `ModuleLoader` wraps the cache and falls back to reading the
file when the entry is missing.
"""

from __future__ import annotations

import importlib.util
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, cast


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


logger = logging.getLogger(__name__)

_CACHE_MISS = object()


class ModuleCache:
    """Remembers loaded Python modules by file path and drops the oldest when full."""

    def __init__(self, maxsize: int = 128) -> None:
        """Create an LRU cache with the given capacity."""
        self._maxsize = maxsize
        self._order: OrderedDict[Path, ModuleType | None] = OrderedDict()

    def get(self, path: Path) -> ModuleType | None | object:
        """Return the cached module, a cached `None`, or the miss sentinel."""
        if path not in self._order:
            return _CACHE_MISS
        self._order.move_to_end(path)
        return self._order[path]

    def set(self, path: Path, module: ModuleType | None) -> None:
        """Store the module (or `None` on failure) under `path`, evicting when full."""
        if path not in self._order and len(self._order) >= self._maxsize:
            self._order.popitem(last=False)
        self._order[path] = module
        self._order.move_to_end(path)

    def clear(self) -> None:
        """Drop every entry from the cache."""
        self._order.clear()

    def __len__(self) -> int:
        """Return the number of cached entries."""
        return len(self._order)

    def __contains__(self, path: Path) -> bool:
        """Return True when `path` currently has a cached entry."""
        return path in self._order


class ModuleLoader:
    """Loads a `.py` file as a module and reuses the last load for the same path."""

    def __init__(self, cache: ModuleCache | None = None) -> None:
        """Bind the loader to a shared or new `ModuleCache`."""
        self._cache = cache or ModuleCache()

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
            return None
        else:
            return module


__all__ = ["ModuleCache", "ModuleLoader"]
