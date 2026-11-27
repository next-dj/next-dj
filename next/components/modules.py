"""LRU cache and loader for `component.py` modules.

The cache spares repeated renders the cost of re-executing module top level.
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from next.caches import BoundedCache, LruCache
from next.permissions.access import declarations
from next.utils import exec_module_file

from .context import awaits, component
from .defer import defer_declarations


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


logger = logging.getLogger(__name__)


class _Miss(enum.Enum):
    """Type of the cache-miss sentinel, so a hit narrows without a cast."""

    MISS = "miss"


_CACHE_MISS: Final = _Miss.MISS

_load_errors: BoundedCache[Path, Exception] = BoundedCache(depends=())


def _detached(exc: Exception) -> Exception:
    """Return `exc` without its traceback and chain.

    A live traceback would pin the failing module's frames while the entry stands.
    """
    exc.__cause__ = None
    exc.__context__ = None
    return exc.with_traceback(None)


def last_load_error(file_path: Path) -> Exception | None:
    """Return what the last import of `file_path` raised, while one stands.

    A failed import renders the component as nothing, so a check names it.
    """
    return _load_errors.get(file_path)


class Loaded(NamedTuple):
    """A loaded module, `None` for a failed import, with what each render reads of it.

    `awaiting` marks an async `render`, `resolves` a `render` or a context function.
    """

    module: ModuleType | None
    awaiting: bool = False
    resolves: bool = False


class ModuleCache:
    """Remembers loaded Python modules by file path and drops the oldest when full.

    A module and its async flag are one entry, so no reader sees one without the other.
    """

    def __init__(self, maxsize: int = 128) -> None:
        """Create an LRU cache with the given capacity."""
        self._order: LruCache[Path, Loaded] = LruCache(maxsize)

    def entry(self, path: Path) -> Loaded | Literal[_Miss.MISS]:
        """Return the cached module with its async flag, or the miss sentinel."""
        return self._order.get(path, _CACHE_MISS)

    def get(self, path: Path) -> ModuleType | Literal[_Miss.MISS] | None:
        """Return the cached module, a cached `None`, or the miss sentinel."""
        entry = self._order.get(path, _CACHE_MISS)
        return entry if entry is _CACHE_MISS else entry.module

    def set(
        self,
        path: Path,
        module: ModuleType | None,
        *,
        awaiting: bool = False,
        resolves: bool = False,
    ) -> None:
        """Store the module (or `None` on failure) under `path`, evicting when full.

        `awaiting` and `resolves` are read on every render, so they are kept with it.
        """
        self._order[path] = Loaded(module, awaiting, resolves)

    def clear(self) -> None:
        """Drop every entry from the cache."""
        self._order.clear()

    def __len__(self) -> int:
        """Return the number of cached entries, so an empty cache is falsy."""
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
        entry = self._cache.entry(path)
        if entry is _CACHE_MISS:
            entry = self._fill(path)
        return entry.module

    def loaded(self, path: Path) -> Loaded:
        """Return the module for `path` with its async-render flag, in one read."""
        entry = self._cache.entry(path)
        if entry is _CACHE_MISS:
            entry = self._fill(path)
        return entry

    def _fill(self, path: Path) -> Loaded:
        """Load `path` from disk and cache the module with its async-render flag."""
        entry = self._load_from_disk(path)
        self._cache.set(
            path, entry.module, awaiting=entry.awaiting, resolves=entry.resolves
        )
        return entry

    def _load_from_disk(self, path: Path) -> Loaded:
        """Execute `path` as a module, answering it with what each render reads of it.

        A failure is recorded for the render to refuse, and declarations swap in after.
        """
        try:
            with declarations.collecting(path), defer_declarations.collecting(path):
                module = exec_module_file(path, f"component_module_{path.stem}")
                render = getattr(module, "render", None)
                awaiting = callable(render) and awaits(render)
        except Exception as exc:
            logger.exception("Could not import component module %s", path)
            _load_errors[path] = _detached(exc)
            return Loaded(None)
        _load_errors.pop(path)
        if callable(render):
            return Loaded(module, awaiting=awaiting, resolves=True)
        return Loaded(module, resolves=bool(component.lookup(path)[0]))


__all__ = ["ModuleCache", "ModuleLoader", "last_load_error"]
