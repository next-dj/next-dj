from collections import OrderedDict
from typing import override

import pytest

from next.caches import DEFAULT_CACHE_SIZE, BoundedCache, LruCache


class _EmptiedEntries(OrderedDict[str, int]):
    """Entries another thread emptied between the write and the eviction."""

    @override
    def popitem(self, last: bool = True) -> tuple[str, int]:
        """Report the cache as empty, whatever it holds."""
        raise KeyError(last)


class _UnreorderableEntries(OrderedDict[str, int]):
    """Entries another thread evicted the written key from before the reorder."""

    @override
    def move_to_end(self, key: str, last: bool = True) -> None:
        """Report the key as gone, whatever the cache holds."""
        raise KeyError(key)


def _emptied(maxsize: int) -> LruCache[str, int]:
    cache: LruCache[str, int] = LruCache(maxsize)
    cache._entries = _EmptiedEntries()
    return cache


def _unreorderable(maxsize: int, **entries: int) -> LruCache[str, int]:
    cache: LruCache[str, int] = LruCache(maxsize)
    cache._entries = _UnreorderableEntries(**entries)
    return cache


class TestBoundedCache:
    """The cache that evicts by age of insert."""

    def test_a_new_key_past_the_bound_drops_the_oldest_insert(self) -> None:
        """The bound is what a cache of one-off keys never grows past."""
        cache: BoundedCache[str, int] = BoundedCache(2)
        for key, value in (("first", 1), ("second", 2), ("third", 3)):
            cache[key] = value
        assert list(cache) == ["second", "third"]

    def test_a_key_under_the_bound_evicts_nothing(self) -> None:
        """A working set below the bound keeps every entry it ever wrote."""
        cache: BoundedCache[str, int] = BoundedCache(8)
        cache["first"] = 1
        cache["second"] = 2
        assert list(cache) == ["first", "second"]

    def test_a_rewrite_keeps_the_key_where_it_was(self) -> None:
        """A rewrite costs no reorder, so the entry keeps the age of its insert."""
        cache: BoundedCache[str, int] = BoundedCache(2)
        cache["first"] = 1
        cache["second"] = 2
        cache["first"] = 10
        assert list(cache) == ["first", "second"]
        assert cache["first"] == 10

    def test_a_read_reorders_nothing(self) -> None:
        """Age of insert ranks the entries, so reading one costs the lookup alone."""
        cache: BoundedCache[str, int] = BoundedCache(2)
        cache["first"] = 1
        cache["second"] = 2
        assert cache.get("first") == 1
        assert list(cache) == ["first", "second"]

    def test_a_miss_answers_the_default(self) -> None:
        """A key no entry is held under answers what the caller asked for."""
        cache: BoundedCache[str, int] = BoundedCache()
        assert cache.get("absent") is None
        assert cache.get("absent", 7) == 7

    def test_a_cache_emptied_in_between_costs_no_error(self) -> None:
        """A concurrent clear leaves the write standing rather than raising."""
        cache: BoundedCache[str, int] = BoundedCache(0)
        cache._entries = _EmptiedEntries()
        cache["only"] = 1
        assert cache["only"] == 1

    def test_membership_and_length_report_what_is_held(self) -> None:
        """The dunders answer for the entries, not for the mapping behind them."""
        cache: BoundedCache[str, int] = BoundedCache()
        cache["only"] = 1
        assert "only" in cache
        assert "absent" not in cache
        assert len(cache) == 1

    def test_dropping_a_key_tolerates_one_that_is_absent(self) -> None:
        """A caller invalidating an entry never has to know whether one is held."""
        cache: BoundedCache[str, int] = BoundedCache()
        cache["only"] = 1
        cache.pop("only")
        cache.pop("only")
        assert not cache

    def test_clearing_drops_every_entry(self) -> None:
        """A cleared cache answers as an empty one."""
        cache: BoundedCache[str, int] = BoundedCache()
        cache["only"] = 1
        cache.clear()
        assert len(cache) == 0

    def test_the_default_bound_is_the_shared_one(self) -> None:
        """One number covers every path-keyed cache of the framework."""
        cache: BoundedCache[str, int] = BoundedCache()
        assert cache._maxsize == DEFAULT_CACHE_SIZE


class TestLruCache:
    """The cache where a read makes its entry the freshest one."""

    def test_a_rewrite_keeps_the_key_and_makes_it_the_freshest(self) -> None:
        """A key already held stays readable while it moves to the end."""
        cache: LruCache[str, int] = LruCache(2)
        cache["first"] = 1
        cache["second"] = 2
        cache["first"] = 10
        assert list(cache) == ["second", "first"]
        assert cache["first"] == 10

    def test_a_new_key_past_the_bound_drops_the_stalest(self) -> None:
        """The bound is what a cache of one-off keys never grows past."""
        cache: LruCache[str, int] = LruCache(2)
        for key, value in (("first", 1), ("second", 2), ("third", 3)):
            cache[key] = value
        assert list(cache) == ["second", "third"]

    def test_a_read_makes_the_entry_the_freshest(self) -> None:
        """The read side of the bound keeps a served entry from being evicted."""
        cache: LruCache[str, int] = LruCache(2)
        cache["first"] = 1
        cache["second"] = 2
        assert cache.get("first") == 1
        assert list(cache) == ["second", "first"]

    def test_a_miss_answers_the_default(self) -> None:
        """A key no entry is held under answers what the caller asked for."""
        cache: LruCache[str, int] = LruCache()
        assert cache.get("absent") is None
        assert cache.get("absent", 7) == 7

    def test_a_cache_emptied_in_between_costs_no_error(self) -> None:
        """A concurrent eviction leaves the write standing rather than raising."""
        cache = _emptied(0)
        cache["only"] = 1
        assert cache._entries["only"] == 1

    def test_a_key_evicted_before_the_reorder_costs_no_error(self) -> None:
        """The reorder is no place to raise, because the write already landed."""
        cache = _unreorderable(8)
        cache["only"] = 1
        assert cache._entries["only"] == 1

    def test_the_write_lands_before_anything_can_go_wrong(self) -> None:
        """A key already held never goes missing for a reader without the lock."""
        cache = _unreorderable(1, first=1, second=2)
        cache["first"] = 10
        assert cache._entries["first"] == 10
        assert len(cache) == 2

    def test_a_read_of_an_entry_a_concurrent_eviction_took_reads_as_a_miss(
        self,
    ) -> None:
        """A rebuild rather than an error, because the entry has become a miss."""
        cache = _unreorderable(8, only=1)
        assert cache.get("only") is None

    def test_a_key_no_mapping_can_hold_raises_rather_than_missing(self) -> None:
        """The TypeError is what tells a caller to inspect its callable afresh."""
        cache: LruCache[object, int] = LruCache()
        unhashable: dict[str, str] = {}
        with pytest.raises(TypeError):
            cache.get(unhashable)
