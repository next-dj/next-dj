import pytest
from django.core.management import call_command
from obs import metrics
from obs.models import MetricSnapshot

from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
)
from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.deps.providers import RegisteredParameterProvider
from next.deps.signals import provider_registered
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_validation_failed,
)
from next.pages.signals import context_registered, page_rendered, template_loaded
from next.server.signals import watch_specs_ready
from next.server.watcher import iter_all_autoreload_watch_specs
from next.static.signals import (
    asset_registered,
    backend_loaded,
    collector_finalized,
    html_injected,
)
from next.testing import SignalRecorder
from next.urls.signals import route_registered, router_reloaded


GROUP_SAMPLES: dict[str, list] = {
    "conf": [settings_reloaded],
    "deps": [provider_registered],
    "pages": [template_loaded, context_registered, page_rendered],
    "urls": [route_registered, router_reloaded],
    "components": [
        component_registered,
        component_backend_loaded,
        component_rendered,
    ],
    "forms": [action_registered, action_dispatched, form_validation_failed],
    "static": [
        asset_registered,
        backend_loaded,
        collector_finalized,
        html_injected,
    ],
    "server": [watch_specs_ready],
}


pytestmark = pytest.mark.django_db


def _walk_dashboard(client) -> None:
    """Hit every observability page so receivers accumulate counters."""
    paths = [
        "/",
        "/stats/",
        "/stats/?window=1m",
        "/stats/pages/",
        "/stats/components/",
        "/stats/forms/",
        "/stats/static/",
    ]
    for url in paths:
        response = client.get(url)
        assert response.status_code == 200


class TestOverview:
    """Overview page exposes headline counters that match metric reads."""

    def test_overview_renders_with_zero_counters(self, client) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert b"Pages rendered" in response.content
        assert b"Components rendered" in response.content
        assert b"Actions dispatched" in response.content

    def test_overview_counters_advance_after_warmup(self, client) -> None:
        client.get("/stats/")
        client.get("/stats/pages/")
        response = client.get("/")
        body = response.content.decode()
        assert "Pages rendered" in body
        assert metrics.total_for_kind("pages.rendered") >= 2


class TestStatsTreeRendersEachSubpage:
    """Every nested stats sub-page renders 200 and shows its title."""

    @pytest.mark.parametrize(
        ("url", "needle"),
        [
            ("/stats/", "Render distribution"),
            ("/stats/pages/", "Per-page render counts"),
            ("/stats/components/", "Per-component render counts"),
            ("/stats/forms/", "Form actions"),
            ("/stats/static/", "Static asset pipeline"),
        ],
    )
    def test_subpage(self, client, url, needle) -> None:
        response = client.get(url)
        assert response.status_code == 200
        assert needle.encode() in response.content


class TestLiveStatsSerializerOverride:
    """The override on `live_stats` wraps the payload in a versioned envelope.

    Sibling serialised keys (`render_rates`, `totals_chart`) are emitted
    by component-level callables and demonstrate the per-key
    granularity the framework guarantees.
    """

    def test_live_stats_carries_envelope(self, client) -> None:
        response = client.get("/stats/")
        body = response.content.decode()
        assert '"live_stats":{"v":1,"data":{' in body

    def test_render_rates_stays_flat_through_global_serializer(self, client) -> None:
        response = client.get("/stats/")
        body = response.content.decode()
        # `render_rates` is owned by `_widgets/render_chart` which has
        # no `serializer=` override, so it travels through the global
        # `JS_CONTEXT_SERIALIZER` and lands flat.
        assert '"render_rates":{' in body
        assert '"render_rates":{"v":1' not in body

    def test_overview_totals_chart_carries_envelope(self, client) -> None:
        response = client.get("/")
        body = response.content.decode()
        assert '"totals_chart":{"v":1,"data":{' in body

    def test_window_querystring_propagates_to_inherit_context(self, client) -> None:
        response = client.get("/stats/?window=1h")
        body = response.content.decode()
        assert "Window: 1h" in body


class TestWindowFilters:
    """`?window=...` actually narrows the aggregation through bucket reads."""

    BASE = "2026-05-08T12:00:00+00:00"

    def test_only_recent_buckets_count_under_one_minute_window(
        self, client, frozen_now
    ) -> None:
        with frozen_now(self.BASE) as traveller:
            metrics.incr("pages.rendered", "/old", by=99)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            metrics.incr("pages.rendered", "/recent", by=2)
            response = client.get("/stats/?window=1m")
            body = response.content.decode()
            # `live_stats.totals.pages` reads through `read_window`, so
            # the 99 from 30 minutes ago is excluded under window=1m.
            # The page-render of `/stats/` itself counts toward the
            # current minute too, so the recent total is at least 2.
            assert '"window":"1m"' in body or '"window": "1m"' in body
            recent = metrics.read_window("pages.rendered", minutes=1)
            assert "/old" not in recent
            assert recent.get("/recent", 0) >= 2

    def test_wider_window_reaches_older_buckets(self, client, frozen_now) -> None:
        with frozen_now(self.BASE) as traveller:
            metrics.incr("pages.rendered", "/old", by=99)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            client.get("/stats/?window=1h")
            wide = metrics.read_window("pages.rendered", minutes=60)
            assert wide["/old"] == 99


class TestJsxAssetPipeline:
    """`.jsx` files are emitted as `<script type="text/babel">` tags.

    The overview page mounts the React sparkline through the custom
    `BabelJsxBackend`. The Chart.js widget on `/stats/` continues to
    travel through the regular `.js` path so both kinds coexist on the
    same dashboard.
    """

    def test_overview_emits_babel_script_tag(self, client) -> None:
        response = client.get("/")
        body = response.content.decode()
        assert '<script type="text/babel"' in body
        assert "sparkline" in body

    def test_overview_loads_react_and_babel_cdn_scripts(self, client) -> None:
        response = client.get("/")
        body = response.content.decode()
        assert "react@18" in body
        assert "babel/standalone" in body

    def test_stats_keeps_chart_js_on_regular_script_path(self, client) -> None:
        response = client.get("/stats/")
        body = response.content.decode()
        assert "chart.umd.min.js" in body


class TestFilterFormDispatch:
    """Submitting the filter form fires `action_dispatched` and redirects."""

    def test_post_redirects_with_window_querystring(self, client) -> None:
        with SignalRecorder(action_dispatched) as recorder:
            response = client.post_action("obs:filter_window", {"window": "1m"})
        assert response.status_code == 302
        assert "window=1m" in response["Location"]
        events = recorder.events_for(action_dispatched)
        assert len(events) == 1
        assert events[0].kwargs["response_status"] == 302


class TestSignalGroupsCovered:
    """A walk through the dashboard increments every signal group at least once."""

    def test_each_group_increments(self, client) -> None:
        recorder_signals = [
            sig for signals in GROUP_SAMPLES.values() for sig in signals
        ]
        with SignalRecorder(*recorder_signals) as recorder:
            # `settings_reloaded`, `provider_registered`, and
            # `watch_specs_ready` only fire on explicit lifecycle events,
            # so the test triggers one of each before the walk so every
            # signal group is exercised inside the same recorder window.
            next_framework_settings.reload()

            class _TestProbeProvider(RegisteredParameterProvider):
                """Throwaway provider just to fire `provider_registered`."""

                def can_handle(self, _param: object, _context: object) -> bool:
                    return False

                def resolve(self, _param: object, _context: object) -> None:
                    return None

            iter_all_autoreload_watch_specs()

            _walk_dashboard(client)
            client.post_action("obs:filter_window", {"window": "5m"})

        for group, signals in GROUP_SAMPLES.items():
            hits = sum(len(recorder.events_for(sig)) for sig in signals)
            assert hits >= 1, (
                f"signal group {group!r} did not increment after walk: {signals}"
            )


class TestFlushMetricsCommand:
    """`flush_metrics` drains the cache and writes one row per counter."""

    def test_flush_persists_counters_and_clears_cache(self, client) -> None:
        _walk_dashboard(client)
        before = len(metrics.read_all())
        assert before > 0

        call_command("flush_metrics")

        after = len(metrics.read_all())
        assert after == 0
        assert MetricSnapshot.objects.count() >= before

    def test_flush_command_is_idempotent_when_empty(self, capsys) -> None:
        assert metrics.read_all() == {}
        call_command("flush_metrics")
        captured = capsys.readouterr()
        assert "nothing to flush" in captured.out
        assert MetricSnapshot.objects.count() == 0

    def test_flush_command_announces_count(self, client, capsys) -> None:
        _walk_dashboard(client)
        before = len(metrics.read_all())
        call_command("flush_metrics")
        captured = capsys.readouterr()
        assert f"flushed {before} counters" in captured.out
