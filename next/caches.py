"""Bounded caches the layers of the framework key their own way.

Each cache owns its bound and its eviction policy, so no caller carries either.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, override


if TYPE_CHECKING:
    from collections.abc import Iterator


# The bound every path-keyed cache shares, far above what a project holds, so it catches
# a caller naming keys without end rather than ranking entries.
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
        """Store `value` under `key` and evict the stalest entry past the bound."""
        self._entries[key] = value
        if len(self._entries) > self._maxsize:
            try:
                self._entries.popitem(last=False)
            except KeyError:
                # No caller holds a lock, so a concurrent clear may have taken the
                # stalest entry already, and with it everything past the bound.
                return

    def __contains__(self, key: object) -> bool:
        """Return whether `key` currently holds an entry."""
        return key in self._entries

    def __iter__(self) -> Iterator[K]:
        """Iterate the held keys, from the stalest to the freshest."""
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
    """A bounded cache where reading refreshes an entry, for keys named without end."""

    __slots__ = ()

    @override
    def __getitem__(self, key: K) -> V:
        """Return the entry under `key` and make it the freshest one."""
        # The read comes first, so a key no mapping can hold still raises the
        # TypeError that tells a caller to inspect afresh rather than the
        # KeyError a reorder answers it with. No reader holds a lock, so an
        # eviction landing in between reads as the miss the key has become,
        # which costs a rebuild rather than an error out of a render.
        value = self._entries[key]
        self._entries.move_to_end(key)
        return value

    @override
    def __setitem__(self, key: K, value: V) -> None:
        """Make `key` the freshest entry and evict the stalest past the bound."""
        self._entries[key] = value
        try:
            self._entries.move_to_end(key)
            if len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
        except KeyError:
            # The write lands first, so a key already held never goes missing for a
            # reader without the lock, and the eviction that took this one has
            # already brought the cache back inside the bound.
            return

    @override
    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the entry under `key` and refresh it, or `default` for a miss.

        An eviction landing between the lookup and the refresh reads as a miss.
        """
        entries = self._entries
        try:
            value = entries[key]
            entries.move_to_end(key)
        except KeyError:
            return default
        return value


__all__ = ["DEFAULT_CACHE_SIZE", "BoundedCache", "LruCache"]
