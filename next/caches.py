"""In-process memos of the framework and the sources whose moves drop them.

A memo attaches to the sources it names, so a reset is one move, not one receiver each.
"""

from __future__ import annotations

import contextlib
import functools
import os
import threading
import weakref
from collections import OrderedDict
from types import MethodType
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast, overload, override
from weakref import WeakSet


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


type Sources = tuple[Source, ...]

# Far above a project's page count, so it only limits memory against endless keys.
DEFAULT_CACHE_SIZE = 2048

PAGE_HEADROOM: Final = 2
"""The entries a `PageCache` holds per routed page, covering its ancestor entries."""

# Serialises `PageCache.fit`, so every page cache ends with the bound of the same call.
_FIT_LOCK = threading.Lock()


class _Pinned:
    """A reset held strongly, read through the same call as a weak one."""

    __slots__ = ("_reset",)

    def __init__(self, reset: Callable[[], object]) -> None:
        """Hold `reset` for the life of the process."""
        self._reset = reset

    def __call__(self) -> Callable[[], object]:
        """Return the held reset."""
        return self._reset


class Source:
    """A named origin of process state, whose move runs every reset attached to it.

    A memo that compares on read, like a page plan, reads `version` instead.
    """

    __slots__ = ("_lock", "_resets", "name", "version")

    def __init__(self, name: str) -> None:
        """Start at version zero with no reset attached."""
        self.name = name
        self.version = 0
        self._lock = threading.Lock()
        self._resets: list[Callable[[], Callable[[], object] | None]] = []
        _SOURCES.append(self)

    def attach(self, reset: Callable[[], object]) -> None:
        """Run `reset` on every move, held weakly when its object can be referenced so.

        A memo owned by an instance then leaves the source together with its owner.
        """
        held: Callable[[], Callable[[], object] | None] = _Pinned(reset)
        if isinstance(reset, MethodType):
            # A slotted object without `__weakref__` keeps its reset pinned instead.
            with contextlib.suppress(TypeError):
                held = weakref.WeakMethod(reset)
        with self._lock:
            self._resets.append(held)

    def move(self) -> None:
        """Advance the version, then run every live reset in the order it attached."""
        with self._lock:
            self.version += 1
            resets = [reset for ref in self._resets if (reset := ref()) is not None]
            if len(resets) < len(self._resets):
                self._resets = [ref for ref in self._resets if ref() is not None]
        for reset in resets:
            reset()

    def forked(self) -> None:
        """Take a free lock, as a forked child inherits one another thread may hold."""
        self._lock = threading.Lock()


_SOURCES: list[Source] = []


def sources() -> tuple[Source, ...]:
    """Return every source of the process, in the order each was made."""
    return tuple(_SOURCES)


SETTINGS: Final = Source("settings")
"""Moves on any Django setting change and on every framework settings reload."""

FRAMEWORK: Final = Source("framework")
"""Moves on a framework settings reload only, for state too costly to drop on any."""

ROUTES: Final = Source("routes")
"""Moves when the routes reload, so whatever was read off the page trees goes."""

FORK: Final = Source("fork")
"""Moves in a forked child, which holds none of its parent's threads and streams."""


def _forked() -> None:
    """Free every source lock, then move `FORK` in the child."""
    for source in _SOURCES:
        source.forked()
    FORK.move()


def _watch_forks() -> None:
    """Move `FORK` in every forked child, nothing on a platform that cannot fork."""
    register = getattr(os, "register_at_fork", None)
    if register is not None:
        register(after_in_child=_forked)


_watch_forks()


class _Epoch:
    """The moves one memo has seen, which a build reads to tell it was overtaken."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        """Start at no move."""
        self.value = 0


def memo[R](
    *depends: Source, maxsize: int | None = None
) -> Callable[[Callable[..., R]], functools._lru_cache_wrapper[R]]:
    """Memoise a function until any source in `depends` moves.

    A hit stays one call of the `functools` cache, and an overtaken build runs again.
    """

    def decorate(function: Callable[..., R]) -> functools._lru_cache_wrapper[R]:
        epoch = _Epoch()

        @functools.wraps(function)
        def build(*args: object, **kwargs: object) -> R:
            seen = epoch.value
            value = function(*args, **kwargs)
            # A move counts before it clears, and the cache stores right after this
            # check, its own lock holding a clear off on a build without the GIL.
            if epoch.value != seen:
                return cached(*args, **kwargs)
            return value

        cached = functools.lru_cache(maxsize=maxsize)(build)

        def reset() -> None:
            epoch.value += 1
            cached.cache_clear()

        for source in depends:
            source.attach(reset)
        return cached

    return decorate


def pure_cache[R](
    maxsize: int | None = None,
) -> Callable[[Callable[..., R]], functools._lru_cache_wrapper[R]]:
    """Memoise a function whose result follows only from its arguments and the code."""
    return functools.lru_cache(maxsize=maxsize)


def instance_dict(owner: object) -> dict[str, object]:
    """Return the instance dict of `owner`, read past its proxy and kept by its resets.

    The owner writes this dict on every reset, so the view holds no state of its own.
    """
    return cast("dict[str, object]", vars(owner))


class BoundedCache[K, V]:
    """A cache that evicts oldest-inserted entries first, cheaper to read than LRU."""

    __slots__ = ("__weakref__", "_entries", "_maxsize")

    def __init__(
        self, maxsize: int = DEFAULT_CACHE_SIZE, *, depends: Sources = ()
    ) -> None:
        """Hold at most `maxsize` entries, cleared when a source in `depends` moves."""
        self._maxsize = maxsize
        self._entries: OrderedDict[K, V] = OrderedDict()
        for source in depends:
            source.attach(self.clear)

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
                # A concurrent clear may have emptied the cache, so it is in bound.
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

    @overload
    def get(self, key: K) -> V | None: ...

    @overload
    def get[D](self, key: K, default: D) -> V | D: ...

    def get(self, key: K, default: object = None) -> object:
        """Return the entry under `key`, or `default` when there is none."""
        return self._entries.get(key, default)

    def pop(self, key: K) -> None:
        """Drop the entry under `key`, tolerating a key that holds none."""
        self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop every entry."""
        self._entries.clear()


class PageCache[K, V](BoundedCache[K, V]):
    """A FIFO cache keyed by page, whose bound follows the routed page count."""

    __slots__ = ()

    _bound: ClassVar[int] = DEFAULT_CACHE_SIZE
    _instances: ClassVar[WeakSet[PageCache[Any, Any]]] = WeakSet()

    def __init__(self, *, depends: Sources = ()) -> None:
        """Start at the bound that the last URL build set."""
        super().__init__(PageCache._bound, depends=depends)
        PageCache._instances.add(self)

    @staticmethod
    def fit(page_count: int) -> None:
        """Set every page cache bound to `PAGE_HEADROOM` entries per routed page.

        The bound never falls below `DEFAULT_CACHE_SIZE`.
        """
        bound = max(DEFAULT_CACHE_SIZE, page_count * PAGE_HEADROOM)
        with _FIT_LOCK:
            if bound == PageCache._bound:
                return
            PageCache._bound = bound
            for cache in list(PageCache._instances):
                cache.rebound(bound)

    def rebound(self, bound: int) -> None:
        """Hold at most `bound` entries from now on, dropping the oldest past it."""
        self._maxsize = bound
        entries = self._entries
        while len(entries) > self._maxsize:
            try:
                entries.popitem(last=False)
            except KeyError:
                return


class LruCache[K, V](BoundedCache[K, V]):
    """A bounded cache that evicts the least recently read entry first."""

    __slots__ = ()

    @override
    def __getitem__(self, key: K) -> V:
        """Return the entry under `key` and mark it as the most recently read."""
        # Lookup first, so an unhashable key raises `TypeError` to bypass the cache.
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
            # A concurrent eviction removed this key and already restored the bound.
            return

    @overload
    def get(self, key: K) -> V | None: ...

    @overload
    def get[D](self, key: K, default: D) -> V | D: ...

    @override
    def get(self, key: K, default: object = None) -> object:
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


class IdentityMemo[V]:
    """Hold one value, kept while the key it was built for is the same object.

    The key and the value share one tuple, so a racing read never pairs them wrong.
    """

    __slots__ = ("_held",)

    def __init__(self) -> None:
        """Start empty."""
        self._held: tuple[object, V] | None = None

    def get(self, key: object, build: Callable[[], V]) -> V:
        """Return the value held for `key`, building and keeping it on a miss."""
        held = self._held
        if held is not None and held[0] is key:
            return held[1]
        value = build()
        self._held = (key, value)
        return value

    def forget(self) -> None:
        """Drop the held value, so the next read builds again."""
        self._held = None


_EMPTY: Final = object()


class Memo[V]:
    """Hold one value built on first read, dropped when a source it depends on moves.

    A build that a move overtakes keeps nothing, so it never outlives the move.
    """

    __slots__ = ("__weakref__", "_epoch", "_value")

    def __init__(self, *, depends: Sources) -> None:
        """Start empty and attach to every source in `depends`."""
        self._value: object = _EMPTY
        self._epoch = 0
        for source in depends:
            source.attach(self.forget)

    def get(self, build: Callable[[], V]) -> V:
        """Return the held value, building and keeping it on the first read."""
        value = self._value
        if value is _EMPTY:
            epoch = self._epoch
            value = build()
            if epoch == self._epoch:
                self._value = value
        return cast("V", value)

    def forget(self) -> None:
        """Drop the held value, so the next read builds again."""
        self._epoch += 1
        self._value = _EMPTY


__all__ = [
    "DEFAULT_CACHE_SIZE",
    "FORK",
    "FRAMEWORK",
    "PAGE_HEADROOM",
    "ROUTES",
    "SETTINGS",
    "BoundedCache",
    "IdentityMemo",
    "LruCache",
    "Memo",
    "PageCache",
    "Source",
    "Sources",
    "instance_dict",
    "memo",
    "pure_cache",
    "sources",
]
