import pytest
from django.core.cache import cache
from obs import metrics
from obs.metrics import BUCKET_PREFIX


pytestmark = pytest.mark.django_db


BASE = "2026-05-08T12:00:00+00:00"


class TestReadWindowBoundaries:
    """`read_window(kind, minutes)` includes events inside `[now - m, now]`."""

    def test_event_inside_window_is_counted(self, frozen_now) -> None:
        with frozen_now(BASE) as traveller:
            metrics.incr("k", "x")
            traveller.move_to("2026-05-08T12:00:30+00:00")
            assert metrics.read_window("k", minutes=1) == {"x": 1}

    def test_event_just_outside_window_is_excluded(self, frozen_now) -> None:
        with frozen_now(BASE) as traveller:
            metrics.incr("k", "x")
            traveller.move_to("2026-05-08T12:02:00+00:00")
            assert metrics.read_window("k", minutes=1) == {}

    def test_event_at_window_edge_is_included(self, frozen_now) -> None:
        with frozen_now(BASE) as traveller:
            metrics.incr("k", "x")
            traveller.move_to("2026-05-08T12:01:00+00:00")
            assert metrics.read_window("k", minutes=1) == {"x": 1}

    def test_separate_buckets_sum_under_wider_window(self, frozen_now) -> None:
        with frozen_now(BASE) as traveller:
            metrics.incr("k", "x", by=2)
            traveller.move_to("2026-05-08T12:01:00+00:00")
            metrics.incr("k", "x", by=3)
            traveller.move_to("2026-05-08T12:02:00+00:00")
            metrics.incr("k", "y", by=1)
            assert metrics.read_window("k", minutes=5) == {"x": 5, "y": 1}

    def test_two_minute_window_drops_oldest_bucket(self, frozen_now) -> None:
        with frozen_now(BASE) as traveller:
            metrics.incr("k", "x", by=10)
            traveller.move_to("2026-05-08T12:01:00+00:00")
            metrics.incr("k", "x", by=2)
            traveller.move_to("2026-05-08T12:03:00+00:00")
            assert metrics.read_window("k", minutes=2) == {"x": 2}


class TestReadWindowConcurrentBumps:
    """Multiple bumps in the same minute land in one bucket atomically."""

    def test_repeated_incr_in_same_minute_lands_in_single_bucket(
        self, frozen_now
    ) -> None:
        with frozen_now(BASE):
            for _ in range(5):
                metrics.incr("k", "x")
            assert metrics.read_window("k", minutes=1) == {"x": 5}
            buckets = [
                key
                for key in cache.get(metrics.INDEX_KEY) or set()
                if key.startswith(BUCKET_PREFIX)
            ]
            assert len(buckets) == 1


class TestReadWindowKindIsolation:
    """Bucket reads filter by kind and ignore unrelated keys."""

    def test_other_kind_bucket_is_not_returned(self, frozen_now) -> None:
        with frozen_now(BASE):
            metrics.incr("alpha", "x", by=2)
            metrics.incr("beta", "x", by=99)
            assert metrics.read_window("alpha", minutes=5) == {"x": 2}

    def test_keys_with_colons_are_round_tripped(self, frozen_now) -> None:
        """Page paths in counters carry colons but rpartition keeps them intact."""
        with frozen_now(BASE):
            metrics.incr("pages.rendered", "/a/b:c/page.py", by=4)
            assert metrics.read_window("pages.rendered", minutes=5) == {
                "/a/b:c/page.py": 4,
            }


class TestReadWindowPruning:
    """Bucket entries are dropped from the index only when the cache evicts them.

    Reading with a narrow window must not evict entries that a later
    read with a wider window still needs.
    """

    def test_narrow_window_read_does_not_evict_older_buckets(self, frozen_now) -> None:
        with frozen_now(BASE) as traveller:
            metrics.incr("k", "x", by=4)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            metrics.read_window("k", minutes=5)
            assert metrics.read_window("k", minutes=60) == {"x": 4}

    def test_buckets_pruned_when_cache_value_is_evicted(self, frozen_now) -> None:
        with frozen_now(BASE):
            metrics.incr("k", "x")
            stamp = "202605081200"
            full = f"{BUCKET_PREFIX}k:x:{stamp}"
            cache.delete(full)
            metrics.read_window("k", minutes=5)
            remaining = cache.get(metrics.INDEX_KEY) or set()
            assert full not in remaining


class TestTopByWindow:
    """`top_by_window` returns descending pairs over the bucketed counters."""

    def test_descending_order(self, frozen_now) -> None:
        with frozen_now(BASE):
            metrics.incr("k", "a", by=3)
            metrics.incr("k", "b", by=7)
            metrics.incr("k", "c", by=1)
            assert metrics.top_by_window("k", minutes=5) == [
                ("b", 7),
                ("a", 3),
                ("c", 1),
            ]

    def test_respects_limit(self, frozen_now) -> None:
        with frozen_now(BASE):
            metrics.incr("k", "a", by=3)
            metrics.incr("k", "b", by=7)
            metrics.incr("k", "c", by=1)
            assert metrics.top_by_window("k", minutes=5, limit=2) == [
                ("b", 7),
                ("a", 3),
            ]
