from __future__ import annotations

from pathlib import Path

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from flags.cache import FLAG_PREFIX, MISSING_SENTINEL, get_cached_flag, invalidate_flag
from flags.metrics import RENDER_INDEX_KEY, record_render, render_counts
from flags.models import Flag
from flags.panels._chunks.feature_guard import component as guard
from flags.panels.admin.page import BulkToggleForm
from flags.providers import WRITE_GATE_FLAG, FlagService, flag_service
from flags.receivers import DENIED_COUNT_KEY, _page_key, access_denied_count

from next.testing import resolve_call


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
        with pytest.raises(LookupError):
            resolve_call(guard.render)


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
        assert _page_key(Path("/src/flags/panels/page.py")) == "/"

    def test_nested_page_joins_segments(self) -> None:
        assert (
            _page_key(Path("/src/flags/panels/admin/metrics/page.py"))
            == "admin/metrics"
        )

    def test_path_without_anchor_falls_back_to_stem(self) -> None:
        assert _page_key(Path("/elsewhere/page.py")) == "page"


class TestFlagService:
    """`FlagService.is_enabled` reads through the flag cache."""

    def test_enabled_flag_is_true(self) -> None:
        Flag.objects.create(name="beta", label="Beta", enabled=True)
        assert FlagService().is_enabled("beta") is True

    def test_disabled_flag_is_false(self) -> None:
        Flag.objects.create(name="beta", label="Beta", enabled=False)
        assert FlagService().is_enabled("beta") is False

    def test_absent_flag_is_false(self) -> None:
        assert FlagService().is_enabled("unknown") is False

    def test_named_dependency_returns_service(self) -> None:
        assert isinstance(flag_service(), FlagService)


class TestWriteGateHook:
    """`BulkToggleForm.check_permissions` denies while the gate flag is off."""

    def test_hook_allows_when_gate_on(self) -> None:
        Flag.objects.create(name=WRITE_GATE_FLAG, label="Writes", enabled=True)
        kwargs = resolve_call(BulkToggleForm.check_permissions)
        assert BulkToggleForm.check_permissions(**kwargs) is None

    def test_hook_denies_when_gate_off(self) -> None:
        Flag.objects.create(name=WRITE_GATE_FLAG, label="Writes", enabled=False)
        kwargs = resolve_call(BulkToggleForm.check_permissions)
        with pytest.raises(PermissionDenied):
            BulkToggleForm.check_permissions(**kwargs)

    def test_hook_denies_when_gate_absent(self) -> None:
        kwargs = resolve_call(BulkToggleForm.check_permissions)
        with pytest.raises(PermissionDenied):
            BulkToggleForm.check_permissions(**kwargs)


class TestAccessDeniedCount:
    """`access_denied_count` reflects the form_access_denied counter."""

    def test_counter_starts_at_zero(self) -> None:
        assert access_denied_count() == 0

    def test_counter_reads_cached_value(self) -> None:
        cache.set(DENIED_COUNT_KEY, 3)
        assert access_denied_count() == 3
