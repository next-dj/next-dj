from __future__ import annotations

import pytest
from django.core.cache import cache
from obs import metrics
from obs.dashboards.stats.page import window
from obs.forms import WINDOW_CHOICES, WindowFilterForm
from obs.metrics import INDEX_KEY, PREFIX
from obs.models import MetricSnapshot
from obs.receivers import on_form_validation_failed, on_template_loaded
from obs.serializers import PydanticJsContextSerializer
from obs.static_policies import InstrumentedDedup

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

    def test_read_all_skips_evicted_entries(self) -> None:
        """`read_all` ignores indexed keys whose values fell out of cache."""
        metrics.incr("group", "alive")
        metrics.incr("group", "evicted")
        cache.delete(f"{PREFIX}group:evicted")
        assert metrics.read_all() == {("group", "alive"): 1}

    def test_flush_skips_evicted_entries(self) -> None:
        """`flush` ignores indexed keys whose values fell out of cache."""
        metrics.incr("group", "alive", by=2)
        metrics.incr("group", "evicted")
        cache.delete(f"{PREFIX}group:evicted")
        rows = metrics.flush()
        assert rows == [("group", "alive", 2)]
        assert cache.get(INDEX_KEY) is None

    def test_flush_empty_index_returns_empty_list(self) -> None:
        assert metrics.flush() == []

    def test_flush_clears_index(self) -> None:
        metrics.incr("group", "key")
        metrics.flush()
        assert metrics.read_all() == {}


class TestMetricSnapshotModel:
    """`MetricSnapshot.__str__` is used by the Django admin and shell."""

    def test_str_includes_kind_key_and_value(self) -> None:
        snap = MetricSnapshot(kind="pages", key="/home/", value=42)
        assert str(snap) == "pages:/home/=42"


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

    def test_unknown_window_value_fails_validation(self) -> None:
        form = WindowFilterForm(data={"window": "1y"})
        assert not form.is_valid()


class TestSerializer:
    """`PydanticJsContextSerializer` falls through to JSON for plain values."""

    def test_dumps_plain_dict(self) -> None:
        payload = PydanticJsContextSerializer().dumps({"k": [1, 2]})
        assert payload == '{"k":[1,2]}'


class TestInstrumentedDedup:
    """`InstrumentedDedup` counts both per-asset keys and dedup hits."""

    def test_first_seen_records_asset_only(self) -> None:
        dedup = InstrumentedDedup()
        asset = StaticAsset(kind="css", url="/static/a.css", inline=None)
        dedup.key(asset)
        assert metrics.read_kind("static.asset") == {"css": 1}
        assert metrics.read_kind("static.dedup") == {}

    def test_repeated_call_records_dedup(self) -> None:
        dedup = InstrumentedDedup()
        asset = StaticAsset(kind="css", url="/static/a.css", inline=None)
        dedup.key(asset)
        dedup.key(asset)
        assert metrics.read_kind("static.asset") == {"css": 2}
        assert metrics.read_kind("static.dedup") == {"css": 1}


class TestReceiverDirectInvocation:
    """Direct calls cover the receiver bodies that wait on rare framework events."""

    def test_template_loaded_receiver_increments_counter(self) -> None:
        on_template_loaded(file_path="/tmp/page.py")
        assert metrics.read_kind("pages.template") == {"/tmp/page.py": 1}

    def test_form_validation_failed_receiver_increments_counter(self) -> None:
        on_form_validation_failed(action_name="obs:filter_window")
        assert metrics.read_kind("forms.validation_failed") == {"obs:filter_window": 1}
