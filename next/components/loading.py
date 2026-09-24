"""LRU cache and loader for `component.py` modules.

The cache spares repeated renders the cost of re-executing module top level.
"""

from __future__ import annotations

import enum
import importlib.util
import logging
from typing import TYPE_CHECKING, Final, Literal

from next.caches import BoundedCache, LruCache


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


logger = logging.getLogger(__name__)


class _Miss(enum.Enum):
    """Type of the cache-miss sentinel, so a hit narrows without a cast."""

    MISS = "miss"


_CACHE_MISS: Final = _Miss.MISS

_load_errors: BoundedCache[Path, Exception] = BoundedCache()


def _detached(exc: Exception) -> Exception:
    """Return `exc` without the traceback and the chain it was raised with.

    The record outlives the import, and a live traceback would pin the frames of the
    failing module and their globals for as long as the entry stands.
    """
    exc.__cause__ = None
    exc.__context__ = None
    return exc.with_traceback(None)


def last_load_error(file_path: Path) -> Exception | None:
    """Return what the last import of `file_path` raised, while one stands.

    A failed import degrades the render to the bare template, so a check names it.
    """
    return _load_errors.get(file_path)


class ModuleCache:
    """Remembers loaded Python modules by file path and drops the oldest when full."""

    def __init__(self, maxsize: int = 128) -> None:
        """Create an LRU cache with the given capacity."""
        self._order: LruCache[Path, ModuleType | None] = LruCache(maxsize)

    def get(self, path: Path) -> ModuleType | Literal[_Miss.MISS] | None:
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
        return cached

    def _load_from_disk(self, path: Path) -> ModuleType | None:
        """Execute `path` as a module, recording whatever its body raised.

        A module body runs arbitrary user code, so every failure degrades the
        render to the bare template instead of reaching the browser as a 500.
        """
        spec = importlib.util.spec_from_file_location(
            f"component_module_{path.stem}", path
        )
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.exception("Could not import component module %s", path)
            _load_errors[path] = _detached(exc)
            return None
        else:
            _load_errors.pop(path)
            return module


__all__ = ["ModuleCache", "ModuleLoader", "last_load_error"]
