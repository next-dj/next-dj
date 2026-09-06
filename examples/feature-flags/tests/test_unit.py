from __future__ import annotations

from pathlib import Path

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from flags.cache import FLAG_PREFIX, MISSING_SENTINEL, get_cached_flag, invalidate_flag
from flags.demo import DEMO_FLAGS, seed_demo
from flags.metrics import RENDER_INDEX_KEY, record_render, render_counts
from flags.models import Flag
from flags.panels._chunks.feature_guard import component as guard
from flags.panels.admin.page import BulkToggleForm
from flags.providers import WRITE_GATE_FLAG, FlagService, flag_service
from flags.receivers import DENIED_COUNT_KEY, _page_key, access_denied_count

from next.testing import resolve_call


pytestmark = pytest.mark.django_db


class TestDemoFlags:
    """The seed module creates the documented flags and stays idempotent."""

    def test_every_demo_flag_is_seeded(self, demo_data) -> None:
        assert dict(Flag.objects.values_list("name", "enabled")) == {
            "beta_checkout": True,
            "dark_sidebar": False,
            "ai_suggestions": False,
            "admin_writes": True,
        }

    def test_write_gate_starts_open(self, demo_data) -> None:
        assert FlagService().is_enabled(WRITE_GATE_FLAG) is True

    def test_command_leaves_hand_made_flags_alone(self, make_flag) -> None:
        make_flag("house_flag", label="House", enabled=True)
        call_command("seed_demo")
        assert Flag.objects.filter(name="house_flag").exists()
        assert Flag.objects.count() == len(DEMO_FLAGS) + 1

    def test_seeding_twice_keeps_one_copy(self, demo_data) -> None:
        seed_demo()
        assert Flag.objects.count() == len(DEMO_FLAGS)


class TestFlagModel:
    """Pure-Python behaviour of the `Flag` model."""

    @pytest.mark.parametrize(
        ("enabled", "expected"),
        [(True, "beta [on]"), (False, "beta [off]")],
        ids=["on", "off"],
    )
    def test_str_shows_the_state(self, enabled, expected) -> None:
        assert str(Flag(name="beta", enabled=enabled)) == expected


class TestFlagCache:
    """`get_cached_flag` reads through LocMemCache and stores a miss sentinel."""

    def test_miss_is_cached_as_sentinel(self) -> None:
        assert get_cached_flag("unknown") is None
        assert cache.get(f"{FLAG_PREFIX}unknown") == MISSING_SENTINEL

    def test_hit_is_cached_and_returned(self, make_flag) -> None:
        flag = make_flag("beta", label="Beta", enabled=True)
        assert get_cached_flag("beta").pk == flag.pk
        assert cache.get(f"{FLAG_PREFIX}beta").pk == flag.pk
        assert get_cached_flag("beta").pk == flag.pk

    def test_invalidate_drops_entry(self, make_flag) -> None:
        make_flag("beta", label="Beta", enabled=True)
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

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/src/flags/panels/page.py", "/"),
            ("/src/flags/panels/admin/metrics/page.py", "admin/metrics"),
            ("/elsewhere/page.py", "page"),
        ],
        ids=["root", "nested", "outside-the-panels-anchor"],
    )
    def test_key_is_derived_from_the_path(self, path: str, expected: str) -> None:
        assert _page_key(Path(path)) == expected


class TestFlagService:
    """`FlagService.is_enabled` reads through the flag cache."""

    @pytest.mark.parametrize(
        ("enabled", "expected"), [(True, True), (False, False)], ids=["on", "off"]
    )
    def test_stored_state_is_reported(self, make_flag, enabled, expected) -> None:
        make_flag("beta", label="Beta", enabled=enabled)
        assert FlagService().is_enabled("beta") is expected

    def test_absent_flag_is_false(self) -> None:
        assert FlagService().is_enabled("unknown") is False

    def test_named_dependency_returns_service(self) -> None:
        assert isinstance(flag_service(), FlagService)


class TestWriteGateHook:
    """`BulkToggleForm.check_permissions` denies while the gate flag is off."""

    def test_hook_allows_when_gate_on(self, write_gate) -> None:
        write_gate(enabled=True)
        kwargs = resolve_call(BulkToggleForm.check_permissions)
        assert BulkToggleForm.check_permissions(**kwargs) is None

    def test_hook_denies_when_gate_off(self, write_gate) -> None:
        write_gate(enabled=False)
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
