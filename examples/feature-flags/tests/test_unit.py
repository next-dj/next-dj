from __future__ import annotations

from django.core.cache import cache
from flags.cache import FLAG_PREFIX, MISSING_SENTINEL, get_cached_flag, invalidate_flag
from flags.metrics import RENDER_INDEX_KEY, record_render, render_counts
from flags.models import Flag


class TestFlagModel:
    """Pure-Python behaviour of the `Flag` model."""

    def test_str_shows_on_or_off(self) -> None:
        assert str(Flag(name="beta", enabled=True)) == "beta [on]"
        assert str(Flag(name="beta", enabled=False)) == "beta [off]"


class TestFlagCache:
    """`get_cached_flag` reads through LocMemCache and stores a miss sentinel."""

    def test_miss_is_cached_as_sentinel(self) -> None:
        assert get_cached_flag("unknown") is None
        assert cache.get(f"{FLAG_PREFIX}unknown") == MISSING_SENTINEL

    def test_hit_is_cached_and_returned(self) -> None:
        flag = Flag.objects.create(name="beta", label="Beta", enabled=True)
        assert get_cached_flag("beta").pk == flag.pk
        assert cache.get(f"{FLAG_PREFIX}beta").pk == flag.pk
        assert get_cached_flag("beta").pk == flag.pk

    def test_invalidate_drops_entry(self) -> None:
        Flag.objects.create(name="beta", label="Beta", enabled=True)
        get_cached_flag("beta")
        invalidate_flag("beta")
        assert cache.get(f"{FLAG_PREFIX}beta") is None

    def test_second_miss_short_circuits_on_sentinel(self) -> None:
        get_cached_flag("unknown")
        assert cache.get(f"{FLAG_PREFIX}unknown") == MISSING_SENTINEL
        assert get_cached_flag("unknown") is None


class TestFlagProviderErrors:
    """Direct `FlagProvider` calls without a flag name must fail loudly."""

    def test_missing_flag_name_raises(self) -> None:
        import inspect

        import pytest
        from flags.panels._chunks.feature_guard import component as guard
        from flags.providers import FlagProvider

        from next.deps.cache import DependencyCache
        from next.deps.context import ResolutionContext

        param = inspect.signature(guard.render).parameters["flag"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )

        with pytest.raises(LookupError):
            FlagProvider().resolve(param, ctx)


class TestRenderCounts:
    """`record_render` bumps per-page counters and tracks the page index."""

    def test_counts_are_aggregated(self) -> None:
        assert record_render("/") == 1
        assert record_render("/") == 2
        assert record_render("admin") == 1
        assert render_counts() == {"/": 2, "admin": 1}

    def test_index_is_deduplicated(self) -> None:
        record_render("demo")
        record_render("demo")
        tracked = cache.get(RENDER_INDEX_KEY)
        assert tracked == {"demo"}

    def test_empty_index_returns_empty_dict(self) -> None:
        assert render_counts() == {}


class TestPageKey:
    """`_page_key` derives a stable per-page identifier from the full path."""

    def test_root_page_is_slash(self) -> None:
        from pathlib import Path

        from flags.receivers import _page_key

        assert _page_key(Path("/src/flags/panels/page.py")) == "/"

    def test_nested_page_joins_segments(self) -> None:
        from pathlib import Path

        from flags.receivers import _page_key

        assert (
            _page_key(Path("/src/flags/panels/admin/metrics/page.py"))
            == "admin/metrics"
        )

    def test_path_without_anchor_falls_back_to_stem(self) -> None:
        from pathlib import Path

        from flags.receivers import _page_key

        assert _page_key(Path("/elsewhere/page.py")) == "page"
