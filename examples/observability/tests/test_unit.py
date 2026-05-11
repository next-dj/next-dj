import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from obs import metrics
from obs.backends import CountingComponentsBackend
from obs.dashboards.page import totals
from obs.dashboards.stats.components.page import counters as components_counters
from obs.dashboards.stats.forms.page import dispatched, validation_failed
from obs.dashboards.stats.page import live_stats, window
from obs.dashboards.stats.pages.page import counters as pages_counters
from obs.dashboards.stats.static.page import assets, collector, dedup
from obs.forms import WINDOW_CHOICES, WindowFilterForm
from obs.metrics import INDEX_KEY, PREFIX
from obs.models import MetricSnapshot
from obs.receivers import (
    on_action_dispatched,
    on_action_registered,
    on_asset_registered,
    on_component_backend_loaded,
    on_component_registered,
    on_component_rendered,
    on_components_registered,
    on_context_registered,
    on_form_validation_failed,
    on_html_injected,
    on_page_rendered,
    on_provider_registered,
    on_route_registered,
    on_router_reloaded,
    on_settings_reloaded,
    on_static_backend_loaded,
    on_template_loaded,
    on_watch_specs_ready,
)
from obs.serializers import PydanticJsContextSerializer, WrappedJsContextSerializer
from obs.static_policies import InstrumentedDedup
from pydantic import BaseModel

from next.components.backends import FileComponentsBackend
from next.static import StaticAsset


pytestmark = pytest.mark.django_db


class TestMetrics:
    """`metrics` exposes the LocMemCache counter API the receivers depend on."""

    def test_first_incr_seeds_counter(self) -> None:
        assert metrics.incr("group", "key") == 1
        assert metrics.read_all() == {("group", "key"): 1}

    def test_repeated_incr_accumulates(self) -> None:
        metrics.incr("group", "key", by=3)
        metrics.incr("group", "key", by=2)
        assert metrics.read_kind("group") == {"key": 5}

    def test_total_for_kind_sums_values(self) -> None:
        metrics.incr("kind", "a", by=4)
        metrics.incr("kind", "b", by=1)
        metrics.incr("other", "c", by=99)
        assert metrics.total_for_kind("kind") == 5

    def test_top_by_kind_returns_descending_pairs(self) -> None:
        metrics.incr("kind", "a", by=2)
        metrics.incr("kind", "b", by=5)
        metrics.incr("kind", "c", by=1)
        assert metrics.top_by_kind("kind") == [("b", 5), ("a", 2), ("c", 1)]

    def test_top_by_kind_respects_limit(self) -> None:
        metrics.incr("kind", "a", by=2)
        metrics.incr("kind", "b", by=5)
        metrics.incr("kind", "c", by=1)
        assert metrics.top_by_kind("kind", limit=2) == [("b", 5), ("a", 2)]

    def test_read_all_skips_evicted_entries(self) -> None:
        metrics.incr("group", "alive")
        metrics.incr("group", "evicted")
        cache.delete(f"{PREFIX}group:evicted")
        assert metrics.read_all() == {("group", "alive"): 1}

    def test_read_all_skips_bucket_entries(self) -> None:
        """Cumulative reads ignore the bucketed half of the index."""
        metrics.incr("group", "x", by=4)
        only_cumulative = metrics.read_all()
        assert only_cumulative == {("group", "x"): 4}

    def test_flush_skips_evicted_entries(self) -> None:
        metrics.incr("group", "alive", by=2)
        metrics.incr("group", "evicted")
        cache.delete(f"{PREFIX}group:evicted")
        rows = metrics.flush()
        assert ("group", "alive", 2) in rows
        assert all(row[1] != "evicted" for row in rows)
        assert cache.get(INDEX_KEY) is None

    def test_flush_empty_index_returns_empty_list(self) -> None:
        assert metrics.flush() == []

    def test_flush_clears_index_and_bucket_entries(self) -> None:
        metrics.incr("group", "key")
        metrics.flush()
        assert metrics.read_all() == {}
        assert metrics.read_window("group", 60) == {}


class TestMetricSnapshotModel:
    """`MetricSnapshot.__str__` is used by the Django admin and shell."""

    def test_str_includes_kind_key_and_value(self) -> None:
        snap = MetricSnapshot(kind="pages", key="/home/", value=42)
        assert str(snap) == "pages:/home/=42"


class TestMetricSnapshotConstraints:
    """Schema-level guarantees that `flush_metrics` relies on."""

    def test_unique_together_rejects_same_triple(self, frozen_now) -> None:
        with frozen_now("2026-05-08T12:00:00+00:00"):
            MetricSnapshot.objects.create(kind="k", key="x", value=1)
            with transaction.atomic(), pytest.raises(IntegrityError):
                MetricSnapshot.objects.create(kind="k", key="x", value=2)

    def test_default_ordering_is_descending_captured_at(self, frozen_now) -> None:
        with frozen_now("2026-05-08T12:00:00+00:00") as traveller:
            first = MetricSnapshot.objects.create(kind="k", key="a", value=1)
            traveller.move_to("2026-05-08T12:01:00+00:00")
            second = MetricSnapshot.objects.create(kind="k", key="b", value=2)
            ordered = list(MetricSnapshot.objects.all())
            assert ordered[0].pk == second.pk
            assert ordered[1].pk == first.pk


class TestWindowContextDefault:
    """`@context("window")` falls back to the default when no request is set."""

    def test_returns_default_without_request(self) -> None:
        assert window(request=None) == "5m"


class TestForm:
    """`WindowFilterForm` exposes the same choices the widget renders."""

    def test_choice_field_has_three_windows(self) -> None:
        form = WindowFilterForm()
        choices = form.fields["window"].choices
        assert list(choices) == list(WINDOW_CHOICES)

    @pytest.mark.parametrize("value", ["1m", "5m", "1h"])
    def test_known_windows_pass_validation(self, value: str) -> None:
        form = WindowFilterForm(data={"window": value})
        assert form.is_valid()

    @pytest.mark.parametrize("value", ["1y", "", "30s", "5"])
    def test_unknown_windows_fail_validation(self, value: str) -> None:
        form = WindowFilterForm(data={"window": value})
        assert not form.is_valid()


class _PydanticPayload(BaseModel):
    name: str
    count: int


class TestSerializerOverride:
    """`WrappedJsContextSerializer` proves the override is visible in HTML."""

    def test_global_pydantic_serializer_dumps_plain_dict(self) -> None:
        payload = PydanticJsContextSerializer().dumps({"k": [1, 2]})
        assert payload == '{"k":[1,2]}'

    def test_global_pydantic_serializer_unwraps_basemodel(self) -> None:
        encoded = PydanticJsContextSerializer().dumps(
            _PydanticPayload(name="x", count=3)
        )
        assert json.loads(encoded) == {"name": "x", "count": 3}

    def test_wrapped_serializer_emits_envelope(self) -> None:
        encoded = WrappedJsContextSerializer().dumps({"a": 1})
        assert json.loads(encoded) == {"v": 1, "data": {"a": 1}}

    def test_wrapped_serializer_supports_basemodel(self) -> None:
        encoded = WrappedJsContextSerializer().dumps(
            _PydanticPayload(name="y", count=7)
        )
        assert json.loads(encoded) == {
            "v": 1,
            "data": {"name": "y", "count": 7},
        }


class TestInstrumentedDedup:
    """`InstrumentedDedup` counts both per-asset keys and dedup hits."""

    def test_first_seen_records_asset_only(self) -> None:
        dedup_strategy = InstrumentedDedup()
        asset = StaticAsset(kind="css", url="/static/a.css", inline=None)
        dedup_strategy.key(asset)
        assert metrics.read_kind("static.asset") == {"css": 1}
        assert metrics.read_kind("static.dedup") == {}

    def test_repeated_call_records_dedup(self) -> None:
        dedup_strategy = InstrumentedDedup()
        asset = StaticAsset(kind="css", url="/static/a.css", inline=None)
        dedup_strategy.key(asset)
        dedup_strategy.key(asset)
        assert metrics.read_kind("static.asset") == {"css": 2}
        assert metrics.read_kind("static.dedup") == {"css": 1}


class TestCountingComponentsBackend:
    """The custom component backend wraps `get_component` to count lookups."""

    def test_resolved_lookup_records_counter(self) -> None:
        backend = CountingComponentsBackend.__new__(CountingComponentsBackend)
        fake_info = type("X", (), {"module_path": None, "name": "stat_card"})()
        with patch.object(
            FileComponentsBackend, "get_component", return_value=fake_info
        ):
            result = backend.get_component("stat_card", Path("/tmp/x.djx"))
        assert result is fake_info
        assert metrics.read_kind("components.lookup") == {"stat_card": 1}

    def test_missing_lookup_does_not_record(self) -> None:
        backend = CountingComponentsBackend.__new__(CountingComponentsBackend)
        with patch.object(FileComponentsBackend, "get_component", return_value=None):
            result = backend.get_component("missing", Path("/tmp/x.djx"))
        assert result is None
        assert metrics.read_kind("components.lookup") == {}


class TestReceiverDirectInvocation:
    """Exhaustive direct-call coverage for every signal-group receiver."""

    @pytest.mark.parametrize(
        ("receiver", "kwargs", "expected_kind", "expected_key", "expected_value"),
        [
            (on_settings_reloaded, {}, "conf", "settings_reloaded", 1),
            (on_provider_registered, {}, "deps", "provider_registered", 1),
            (
                on_template_loaded,
                {"file_path": "/tmp/page.py"},
                "pages.template",
                "/tmp/page.py",
                1,
            ),
            (
                on_context_registered,
                {"file_path": "/tmp/page.py"},
                "pages.context",
                "/tmp/page.py",
                1,
            ),
            (
                on_page_rendered,
                {"file_path": "/tmp/page.py", "duration_ms": None},
                "pages.rendered",
                "/tmp/page.py",
                1,
            ),
            (
                on_route_registered,
                {"url_path": "/stats/"},
                "urls.route",
                "/stats/",
                1,
            ),
            (on_router_reloaded, {}, "urls", "router_reloaded", 1),
            (on_component_backend_loaded, {}, "components", "backend_loaded", 1),
            (
                on_action_registered,
                {"action_name": "obs:filter_window"},
                "forms.action_registered",
                "obs:filter_window",
                1,
            ),
            (
                on_action_dispatched,
                {"action_name": "obs:filter_window"},
                "forms.action_dispatched",
                "obs:filter_window",
                1,
            ),
            (
                on_form_validation_failed,
                {"action_name": "obs:filter_window"},
                "forms.validation_failed",
                "obs:filter_window",
                1,
            ),
            (on_asset_registered, {}, "static", "asset_registered", 1),
            (on_static_backend_loaded, {}, "static", "backend_loaded", 1),
            (on_html_injected, {"injected_bytes": None}, "static", "html_injected", 1),
            (on_watch_specs_ready, {}, "server", "watch_specs_ready", 1),
        ],
    )
    def test_receiver_increments_expected_counter(
        self,
        receiver,
        kwargs,
        expected_kind,
        expected_key,
        expected_value,
    ) -> None:
        receiver(**kwargs)
        assert metrics.read_kind(expected_kind).get(expected_key) == expected_value

    def test_page_rendered_with_duration_accumulates_milliseconds(self) -> None:
        on_page_rendered(file_path="/tmp/page.py", duration_ms=42)
        assert metrics.read_kind("pages.duration_ms_total") == {"/tmp/page.py": 42}

    def test_html_injected_with_bytes_accumulates_total(self) -> None:
        on_html_injected(injected_bytes=1024)
        assert metrics.read_kind("static") == {
            "html_injected": 1,
            "injected_bytes_total": 1024,
        }

    def test_component_registered_uses_info_name(self) -> None:
        info = type("X", (), {"name": "stat_card"})()
        on_component_registered(info=info)
        assert metrics.read_kind("components.registered") == {"stat_card": 1}

    def test_components_registered_counts_each_info_in_batch(self) -> None:
        a = type("X", (), {"name": "stat_card"})()
        b = type("X", (), {"name": "header"})()
        on_components_registered(infos=(a, b, a))
        assert metrics.read_kind("components.registered") == {
            "stat_card": 2,
            "header": 1,
        }

    def test_components_registered_empty_batch_is_noop(self) -> None:
        on_components_registered(infos=())
        assert metrics.read_kind("components.registered") == {}

    def test_component_rendered_uses_info_name(self) -> None:
        info = type("X", (), {"name": "stat_card"})()
        on_component_rendered(info=info)
        assert metrics.read_kind("components.rendered") == {"stat_card": 1}


class TestContexts:
    """Direct coverage of every page and component context callable."""

    def test_overview_totals_aggregates_each_kind(self) -> None:
        metrics.incr("pages.rendered", "/a")
        metrics.incr("pages.rendered", "/b", by=2)
        metrics.incr("components.rendered", "stat_card")
        metrics.incr("forms.action_dispatched", "obs:filter_window")
        metrics.incr("static", "html_injected", by=4)
        result = totals()
        assert result == {
            "pages_rendered": 3,
            "components_rendered": 1,
            "actions_dispatched": 1,
            "html_injections": 4,
        }

    def test_pages_counters_sorted_descending(self) -> None:
        metrics.incr("pages.rendered", "/a")
        metrics.incr("pages.rendered", "/b", by=5)
        assert pages_counters() == [("/b", 5), ("/a", 1)]

    def test_components_counters_sorted_descending(self) -> None:
        metrics.incr("components.rendered", "stat_card", by=2)
        metrics.incr("components.rendered", "filter_window")
        assert components_counters() == [("stat_card", 2), ("filter_window", 1)]

    def test_forms_dispatched_and_validation_failed(self) -> None:
        metrics.incr("forms.action_dispatched", "obs:filter_window", by=3)
        metrics.incr("forms.validation_failed", "obs:filter_window")
        assert dispatched() == [("obs:filter_window", 3)]
        assert validation_failed() == [("obs:filter_window", 1)]

    def test_static_dedup_assets_collector(self) -> None:
        metrics.incr("static.dedup", "css", by=2)
        metrics.incr("static.asset", "js", by=4)
        metrics.incr("static", "html_injected", by=7)
        metrics.incr("static", "collector_finalized", by=2)
        metrics.incr("static", "injected_bytes_total", by=1024)
        assert dedup() == [("css", 2)]
        assert assets() == [("js", 4)]
        assert collector() == {
            "html_injected": 7,
            "injected_bytes_total": 1024,
            "collector_finalized": 2,
        }

    def test_live_stats_returns_zero_totals_without_data(self) -> None:
        result = live_stats(window="5m")
        assert result == {
            "window": "5m",
            "minutes": 5,
            "totals": {"pages": 0, "components": 0, "actions": 0},
        }

    def test_live_stats_unknown_window_falls_back_to_default(self) -> None:
        result = live_stats(window="bogus")
        assert result["minutes"] == 5
