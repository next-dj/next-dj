import gc
import os
import threading
from collections import OrderedDict
from types import SimpleNamespace
from typing import override
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from next import caches
from next.caches import (
    DEFAULT_CACHE_SIZE,
    FORK,
    FRAMEWORK,
    PAGE_HEADROOM,
    ROUTES,
    SETTINGS,
    BoundedCache,
    IdentityMemo,
    LruCache,
    Memo,
    PageCache,
    Source,
    instance_dict,
    memo,
    pure_cache,
    sources,
)
from next.conf import next_framework_settings


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
        """A missing key returns the default the caller passed."""
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
        """The dunder methods report the entries, not the mapping behind them."""
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
        """A cleared cache behaves as an empty one."""
        cache: BoundedCache[str, int] = BoundedCache()
        cache["only"] = 1
        cache.clear()
        assert len(cache) == 0

    def test_the_default_bound_is_the_shared_one(self) -> None:
        """One number covers every path-keyed cache of the framework."""
        cache: BoundedCache[str, int] = BoundedCache()
        assert cache._maxsize == DEFAULT_CACHE_SIZE


@pytest.fixture()
def restored_page_bound():
    """Put the shared page bound back on the class and on every live cache."""
    bound = PageCache._bound
    yield
    PageCache._bound = bound
    for cache in list(PageCache._instances):
        cache.rebound(bound)


def _fill(cache: PageCache[int, int], count: int) -> None:
    for key in range(count):
        cache[key] = key


@pytest.mark.usefixtures("restored_page_bound")
class TestPageCache:
    """The bound of a page-keyed cache follows the routed page count."""

    def test_fit_raises_the_bound_of_every_cache_held(self) -> None:
        cache: PageCache[int, int] = PageCache()
        pages = PageCache._bound
        PageCache.fit(pages)
        _fill(cache, pages * PAGE_HEADROOM + 1)
        assert len(cache) == pages * PAGE_HEADROOM
        assert 0 not in cache
        assert 1 in cache

    def test_a_cache_built_after_fit_starts_at_the_raised_bound(self) -> None:
        pages = PageCache._bound
        PageCache.fit(pages)
        cache: PageCache[int, int] = PageCache()
        _fill(cache, pages * PAGE_HEADROOM)
        assert len(cache) == pages * PAGE_HEADROOM

    def test_fit_lowers_the_bound_and_drops_the_oldest_entries(self) -> None:
        cache: PageCache[int, int] = PageCache()
        PageCache.fit(DEFAULT_CACHE_SIZE)
        _fill(cache, DEFAULT_CACHE_SIZE * PAGE_HEADROOM)
        PageCache.fit(1)
        assert cache._maxsize == PageCache._bound == DEFAULT_CACHE_SIZE
        assert len(cache) == DEFAULT_CACHE_SIZE
        assert 0 not in cache
        assert DEFAULT_CACHE_SIZE * PAGE_HEADROOM - 1 in cache

    def test_the_bound_never_falls_below_the_default(self) -> None:
        PageCache.fit(DEFAULT_CACHE_SIZE)
        PageCache.fit(0)
        assert PageCache._bound == DEFAULT_CACHE_SIZE

    def test_a_trim_stops_once_another_thread_emptied_the_cache(self) -> None:
        cache: PageCache[str, int] = PageCache()
        cache._entries = _EmptiedEntries(first=1, second=2)
        cache.rebound(1)
        assert len(cache) == 2


class TestLruCache:
    """The cache where a read makes its entry the most recent one."""

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
        """A missing key returns the default the caller passed."""
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
        """The TypeError tells a caller to bypass the cache for its callable."""
        cache: LruCache[object, int] = LruCache()
        unhashable: dict[str, str] = {}
        with pytest.raises(TypeError):
            cache.get(unhashable)


class TestIdentityMemo:
    """The memo builds once per key object and again for another object."""

    def test_a_repeat_with_the_same_key_builds_once(self) -> None:
        memo: IdentityMemo[list[int]] = IdentityMemo()
        key = object()
        first = memo.get(key, lambda: [1])
        assert memo.get(key, lambda: [2]) is first

    def test_an_equal_key_is_not_the_same_key(self) -> None:
        memo: IdentityMemo[str] = IdentityMemo()
        memo.get([1], lambda: "first")
        assert memo.get([1], lambda: "second") == "second"

    def test_a_new_key_replaces_the_held_one(self) -> None:
        memo: IdentityMemo[str] = IdentityMemo()
        old = object()
        memo.get(old, lambda: "old")
        memo.get(object(), lambda: "new")
        assert memo.get(old, lambda: "again") == "again"

    def test_forget_builds_again(self) -> None:
        memo: IdentityMemo[str] = IdentityMemo()
        key = object()
        memo.get(key, lambda: "before")
        memo.forget()
        assert memo.get(key, lambda: "after") == "after"


class _Slotted:
    """An owner without `__weakref__`, whose reset the source pins."""

    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls = 0

    def forget(self) -> None:
        self.calls += 1


class _Owner:
    """An owner a source references weakly through its bound reset."""

    def __init__(self) -> None:
        self.calls = 0

    def forget(self) -> None:
        self.calls += 1


@pytest.fixture()
def source() -> Source:
    made = Source("test")
    yield made
    caches._SOURCES.remove(made)


class TestSource:
    """A source runs what attached to it on every move, in the order it attached."""

    def test_a_move_advances_the_version_and_runs_each_reset_in_order(
        self, source: Source
    ) -> None:
        order: list[str] = []
        source.attach(lambda: order.append("first"))
        source.attach(lambda: order.append("second"))
        source.move()
        assert source.version == 1
        assert order == ["first", "second"]

    def test_a_reset_of_an_owner_leaves_with_its_owner(self, source: Source) -> None:
        owner = _Owner()
        source.attach(owner.forget)
        source.move()
        assert owner.calls == 1
        del owner
        gc.collect()
        source.move()
        assert source._resets == []

    def test_a_slotted_owner_keeps_its_reset_pinned(self, source: Source) -> None:
        owner = _Slotted()
        source.attach(owner.forget)
        source.move()
        assert owner.calls == 1

    def test_every_source_is_listed(self, source: Source) -> None:
        assert sources()[:4] == (SETTINGS, FRAMEWORK, ROUTES, FORK)
        assert sources()[-1] is source

    def test_a_forked_child_takes_free_locks_and_moves_fork(self) -> None:
        held = SETTINGS._lock
        version = FORK.version
        caches._forked()
        assert SETTINGS._lock is not held
        assert FORK.version == version + 1

    def test_a_platform_that_forks_hears_of_every_child(self) -> None:
        with patch.object(os, "register_at_fork", create=True) as register:
            caches._watch_forks()
        register.assert_called_once_with(after_in_child=caches._forked)

    def test_a_platform_without_fork_registers_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(os, "register_at_fork", raising=False)
        caches._watch_forks()


class TestSettingsMoves:
    """Any setting change moves `SETTINGS`, a framework reload moves both."""

    def test_a_django_setting_moves_settings_only(self) -> None:
        settings_version, framework_version = SETTINGS.version, FRAMEWORK.version
        with override_settings(USE_TZ=False):
            assert SETTINGS.version == settings_version + 1
        assert FRAMEWORK.version == framework_version

    def test_a_framework_reload_moves_both(self) -> None:
        settings_version, framework_version = SETTINGS.version, FRAMEWORK.version
        next_framework_settings.reload()
        assert SETTINGS.version == settings_version + 1
        assert FRAMEWORK.version == framework_version + 1


class TestMemo:
    """A memoised function keeps its answer until a source it names moves."""

    def test_the_answer_holds_until_the_source_moves(self, source: Source) -> None:
        build = Mock(side_effect=[1, 2])
        read = memo(source)(build)
        assert read() == read() == 1
        source.move()
        assert read() == 2

    def test_a_bounded_memo_drops_the_oldest_answer(self, source: Source) -> None:
        read = memo(source, maxsize=1)(lambda key: [key])
        first = read("a")
        read("b")
        assert read("a") is not first

    def test_an_answer_a_move_overtakes_is_not_kept(self, source: Source) -> None:
        state = {"value": "old"}
        entered, release = threading.Event(), threading.Event()
        builds: list[str] = []

        @memo(source)
        def read() -> str:
            value = state["value"]
            builds.append(value)
            if value == "old":
                entered.set()
                release.wait()
            return value

        answers: list[str] = []
        reader = threading.Thread(target=lambda: answers.append(read()))
        reader.start()
        entered.wait()
        state["value"] = "new"
        source.move()
        release.set()
        reader.join()
        assert answers == ["new"]
        assert read() == "new"
        assert builds == ["old", "new"]

    def test_a_pure_cache_answers_once_per_argument(self) -> None:
        build = Mock(side_effect=lambda key: key * 2)
        read = pure_cache(4)(build)
        assert read(2) == read(2) == 4
        build.assert_called_once_with(2)


class TestHeldMemo:
    """`Memo` holds one value and keeps no build a move overtook."""

    def test_the_value_holds_until_the_source_moves(self, source: Source) -> None:
        held: Memo[object] = Memo(depends=(source,))
        first = held.get(object)
        assert held.get(object) is first
        source.move()
        assert held.get(object) is not first

    def test_a_build_a_move_overtakes_is_not_kept(self, source: Source) -> None:
        held: Memo[int] = Memo(depends=(source,))

        def build() -> int:
            source.move()
            return 1

        assert held.get(build) == 1
        assert held.get(lambda: 2) == 2

    def test_a_cache_clears_when_its_source_moves(self, source: Source) -> None:
        cache: BoundedCache[str, int] = BoundedCache(depends=(source,))
        cache["a"] = 1
        source.move()
        assert "a" not in cache


class TestInstanceDict:
    """The view is the owner's own dict, so the owner's writes show through."""

    def test_the_view_follows_the_owner(self) -> None:
        owner = SimpleNamespace(value=1)
        view = instance_dict(owner)
        owner.value = 2
        assert view["value"] == 2
