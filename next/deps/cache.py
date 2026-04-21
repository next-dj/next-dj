"""Sentinels, cycle error, and per-resolution cache used during DI resolution.

The `DependencyCache` object accumulates resolved dependency values
during a single resolution pass. The `_IN_PROGRESS` and `_CACHE_MISS`
sentinels separate the three cache-lookup outcomes (hit, miss, and
in-progress) without collapsing `None`-valued hits into misses.
"""

from __future__ import annotations

from typing import Any


_IN_PROGRESS: object = object()
_CACHE_MISS: object = object()


class DependencyCycleError(Exception):
    """Raised when dependency resolution re-enters a key already in progress."""

    def __init__(self, cycle: list[str]) -> None:
        """Record the offending dependency chain for the error message."""
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


class DependencyCache:
    """Store resolved dependency values and detect cycles via in-progress keys."""

    def __init__(self, backing_dict: dict[str, Any] | None = None) -> None:
        """Initialise storage, optionally sharing an externally owned dict."""
        self._cache: dict[str, Any] = backing_dict if backing_dict is not None else {}
        self._in_progress: set[str] = set()
        self._owns_cache = backing_dict is None

    def get(self, key: str) -> object:
        """Return the cached value, `_IN_PROGRESS`, or `_CACHE_MISS`."""
        if key in self._in_progress:
            return _IN_PROGRESS
        if key in self._cache:
            return self._cache[key]
        return _CACHE_MISS

    def set(self, key: str, value: object) -> None:
        """Store a finished resolution under the given key."""
        self._cache[key] = value
        self._in_progress.discard(key)

    def mark_in_progress(self, key: str) -> None:
        """Mark the key as currently being resolved for cycle detection."""
        self._in_progress.add(key)

    def unmark_in_progress(self, key: str) -> None:
        """Clear the in-progress marker for the key."""
        self._in_progress.discard(key)

    def is_in_progress(self, key: str) -> bool:
        """Return True while the key is mid-resolution."""
        return key in self._in_progress

    def __len__(self) -> int:
        """Return the number of stored values."""
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Return membership in the backing dict."""
        return key in self._cache
