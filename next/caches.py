"""Bounded in-process caches that the framework layers use for their memos.

Each cache holds its own bound and eviction policy, so a caller manages neither.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, override


if TYPE_CHECKING:
    from collections.abc import Iterator


# The default bound of a path-keyed cache, far above the page count of a project.
# It limits memory when a caller creates keys without end, rather than ranking entries.
DEFAULT_CACHE_SIZE = 2048


class BoundedCache[K, V]:
    """A cache that evicts oldest-inserted entries first, cheaper to read than LRU."""

    __slots__ = ("_entries", "_maxsize")

    def __init__(self, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        """Hold at most `maxsize` entries."""
        self._maxsize = maxsize
        self._entries: OrderedDict[K, V] = OrderedDict()

    def __getitem__(self, key: K) -> V:
        """Return the entry under `key`, raising `KeyError` when there is none."""
        return self._entries[key]

    def __setitem__(self, key: K, value: V) -> None:
        """Store `value` under `key` and evict the oldest entry once past the bound."""
        self._entries[key] = value
        if len(self._entries) > self._maxsize:
            try:
                self._entries.popitem(last=False)
            except KeyError:
                # No caller holds a lock, so a concurrent clear may have emptied the
                # cache already, which also brings it back within the bound.
                return

    def __contains__(self, key: object) -> bool:
        """Return whether `key` currently holds an entry."""
        return key in self._entries

    def __iter__(self) -> Iterator[K]:
        """Iterate the held keys, from the oldest to the newest."""
        return iter(self._entries)

    def __len__(self) -> int:
        """Return how many entries are held."""
        return len(self._entries)

    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the entry under `key`, or `default` when there is none."""
        return self._entries.get(key, default)

    def pop(self, key: K) -> None:
        """Drop the entry under `key`, tolerating a key that holds none."""
        self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop every entry."""
        self._entries.clear()


class LruCache[K, V](BoundedCache[K, V]):
    """A bounded cache that evicts the least recently read entry first."""

    __slots__ = ()

    @override
    def __getitem__(self, key: K) -> V:
        """Return the entry under `key` and mark it as the most recently read."""
        # The lookup runs first, so an unhashable key raises `TypeError`, which tells
        # a caller to bypass the cache, where `move_to_end` would raise `KeyError`.
        # No reader holds a lock, so a concurrent eviction between the two calls
        # raises `KeyError`, which a caller treats as a miss and rebuilds.
        value = self._entries[key]
        self._entries.move_to_end(key)
        return value

    @override
    def __setitem__(self, key: K, value: V) -> None:
        """Store `value` as the newest entry and evict the oldest past the bound."""
        self._entries[key] = value
        try:
            self._entries.move_to_end(key)
            if len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
        except KeyError:
            # The write runs first, so a held key never goes missing for a reader. A
            # concurrent eviction that removed this key has already restored the bound.
            return

    @override
    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the entry under `key` and refresh it, or `default` for a miss.

        A concurrent eviction between the lookup and the refresh counts as a miss.
        """
        entries = self._entries
        try:
            value = entries[key]
            entries.move_to_end(key)
        except KeyError:
            return default
        return value


__all__ = ["DEFAULT_CACHE_SIZE", "BoundedCache", "LruCache"]
