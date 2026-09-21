from __future__ import annotations

from itertools import cycle
from pathlib import Path

import pytest

from next.caches import BoundedCache, LruCache


_WARM = 512
_SMALL = 64

# Far past the bound, so a cycled key is one the cache evicted long ago rather
# than one it still holds, and every timed write is an eviction.
_OVERFLOW = 4096


def _keys(count: int, prefix: str = "section") -> list[Path]:
    """Path keys, because every memo built on these caches keys on a page path."""
    return [Path(f"/srv/site/pages/{prefix}_{index}/page.py") for index in range(count)]


def _filled[C: BoundedCache[Path, int]](cache: C, keys: list[Path]) -> C:
    for index, key in enumerate(keys):
        cache[key] = index
    return cache


def _evicting_writes(cache: BoundedCache[Path, int]):
    """Return a callable writing one never-held key per round, keys prebuilt."""
    keys = cycle(_keys(_OVERFLOW, prefix="overflow"))

    def run() -> None:
        cache[next(keys)] = 0

    return run


class TestBenchBoundedCache:
    """`BoundedCache` backs the load-error and resolved-path memos."""

    @pytest.mark.benchmark(group="caches.bounded")
    def test_hit(self, benchmark) -> None:
        keys = _keys(_WARM)
        cache = _filled(BoundedCache[Path, int](), keys)
        benchmark(cache.get, keys[_WARM // 2])

    @pytest.mark.benchmark(group="caches.bounded")
    def test_miss(self, benchmark) -> None:
        cache = _filled(BoundedCache[Path, int](), _keys(_WARM))
        absent = Path("/srv/site/pages/absent/page.py")
        benchmark(cache.get, absent)

    @pytest.mark.benchmark(group="caches.bounded")
    def test_write_under_the_bound(self, benchmark) -> None:
        cache = _filled(BoundedCache[Path, int](), _keys(_WARM))
        key = Path("/srv/site/pages/rewritten/page.py")
        benchmark(cache.__setitem__, key, 1)

    @pytest.mark.benchmark(group="caches.bounded")
    def test_eviction(self, benchmark) -> None:
        cache = _filled(BoundedCache[Path, int](maxsize=_SMALL), _keys(_SMALL))
        benchmark(_evicting_writes(cache))


class TestBenchLruCache:
    """`LruCache` backs the plan cache, the page-root memo and the module memo."""

    @pytest.mark.benchmark(group="caches.lru")
    def test_hit(self, benchmark) -> None:
        keys = _keys(_WARM)
        cache = _filled(LruCache[Path, int](), keys)
        benchmark(cache.get, keys[_WARM // 2])

    @pytest.mark.benchmark(group="caches.lru")
    def test_miss(self, benchmark) -> None:
        cache = _filled(LruCache[Path, int](), _keys(_WARM))
        absent = Path("/srv/site/pages/absent/page.py")
        benchmark(cache.get, absent)

    @pytest.mark.benchmark(group="caches.lru")
    def test_subscript_hit(self, benchmark) -> None:
        """The hot paths subscript rather than `get`, and the reorder is the cost."""
        keys = _keys(_WARM)
        cache = _filled(LruCache[Path, int](), keys)
        benchmark(cache.__getitem__, keys[_WARM // 2])

    @pytest.mark.benchmark(group="caches.lru")
    def test_eviction(self, benchmark) -> None:
        cache = _filled(LruCache[Path, int](maxsize=_SMALL), _keys(_SMALL))
        benchmark(_evicting_writes(cache))
