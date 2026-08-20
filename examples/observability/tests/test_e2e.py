import json
import re

import pytest
from django.core.management import call_command
from django.test import override_settings
from obs import metrics
from obs.models import MetricSnapshot

from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)
from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.deps import RegisteredParameterProvider
from next.deps.signals import provider_registered
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_validation_failed,
)
from next.pages.signals import context_registered, page_rendered, template_loaded
from next.server import iter_all_autoreload_watch_specs
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    backend_loaded,
    collector_finalized,
    html_injected,
)
from next.testing import SignalRecorder, envelope_of
from next.urls.signals import route_registered, router_reloaded


GROUP_SAMPLES: dict[str, list] = {
    "conf": [settings_reloaded],
    "deps": [provider_registered],
    "pages": [template_loaded, context_registered, page_rendered],
    "urls": [route_registered, router_reloaded],
    "components": [
        component_registered,
        components_registered,
        component_backend_loaded,
        component_rendered,
    ],
    "forms": [action_registered, action_dispatched, form_validation_failed],
    "static": [asset_registered, backend_loaded, collector_finalized, html_injected],
    "server": [watch_specs_ready],
}


pytestmark = pytest.mark.django_db


DASHBOARD_PATHS: tuple[str, ...] = (
    "/",
    "/stats/",
    "/stats/?window=1m",
    "/stats/pages/",
    "/stats/components/",
    "/stats/forms/",
    "/stats/static/",
)

_INIT_PAYLOAD = re.compile(r"Next\._init\((.*?)\);</script>")

_ZONE_ATTR = re.compile(r'data-next-zone="([^"]+)"')


def _walk_dashboard(client) -> None:
    """Hit every observability page so receivers accumulate counters."""
    for url in DASHBOARD_PATHS:
        response = client.get(url)
        assert response.status_code == 200


def _init_payload(html: str) -> dict:
    """Return the decoded `Next._init(...)` payload of a rendered page."""
    match = _INIT_PAYLOAD.search(html)
    assert match is not None, "Next._init call missing"
    return json.loads(match.group(1))


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
        """`render_chart` declares no `serializer=`, so its key lands flat."""
        response = client.get("/stats/")
        body = response.content.decode()
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
        """A bucket 30 minutes old falls outside the one-minute read window.

        The render of `/stats/` itself lands in the current minute, so the
        recent counter is at least the two increments seeded here.
        """
        with frozen_now(self.BASE) as traveller:
            metrics.incr("pages.rendered", "/old", by=99)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            metrics.incr("pages.rendered", "/recent", by=2)
            response = client.get("/stats/?window=1m")
            body = response.content.decode()
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
        assert body.index("chart.umd.min.js") < body.index(
            "/static/next/components/render_chart.js"
        )


class TestDevFlagChannel:
    """The framework owns `$dev` in the init payload and gates it on DEBUG."""

    @override_settings(DEBUG=True)
    def test_debug_render_announces_the_dev_flag(self, client) -> None:
        payload = _init_payload(client.get("/").content.decode())
        assert payload["$dev"] is True

    @override_settings(DEBUG=False)
    def test_production_render_omits_the_dev_flag(self, client) -> None:
        payload = _init_payload(client.get("/").content.decode())
        assert "$dev" not in payload
        assert "$csrf" in payload

    @override_settings(DEBUG=True)
    def test_dashboard_context_keys_travel_beside_the_reserved_ones(
        self, client
    ) -> None:
        payload = _init_payload(client.get("/stats/").content.decode())
        assert "live_stats" in payload
        assert payload["$dev"] is True


class TestSparklineStaysOutsideEveryZone:
    """No zone on the dashboard renders the sparkline mount.

    The `next.W074` silencing in `config/settings.py` is honest only while
    the Babel-compiled `component.jsx` never has to ride a patch envelope,
    so this walk fails the moment the widget is pulled into a zone.
    """

    def test_full_render_owns_the_sparkline(self, client) -> None:
        assert "sparkline-mount" in client.get("/").content.decode()

    def test_no_zone_body_carries_the_sparkline_mount(self, client) -> None:
        checked: list[str] = []
        for url in DASHBOARD_PATHS:
            body = client.get(url).content.decode()
            for zone in sorted(set(_ZONE_ATTR.findall(body))):
                html = envelope_of(client.get_zones(url, zone)).html_for_zone(zone)
                assert "sparkline-mount" not in html
                checked.append(zone)
        assert set(checked) >= {"overview-totals", "busiest-pages", "live-totals"}


class TestFilterFormDispatch:
    """Submitting the filter form fires `action_dispatched` and redirects."""

    def test_select_renders_with_seeded_window(self, client) -> None:
        body = client.get("/stats/?window=1h").content.decode()
        assert '<select name="window"' in body
        assert 'value="1h" selected' in body

    def test_post_redirects_with_window_querystring(self, client) -> None:
        with SignalRecorder(action_dispatched) as recorder:
            response = client.post_action("window_filter_form", {"window": "1m"})
        assert response.status_code == 302
        assert "window=1m" in response["Location"]
        events = recorder.events_for(action_dispatched)
        assert len(events) == 1
        assert events[0].kwargs["response_status"] == 302


class TestPollZone:
    """The overview totals zone carries a poll interval and still morphs."""

    def test_overview_zone_carries_poll_interval(self, client) -> None:
        body = client.get("/").content.decode()
        assert 'data-next-poll="5000"' in body
        assert 'data-next-zone="overview-totals"' in body

    def test_overview_zone_get_still_morphs(self, client) -> None:
        response = client.get_zones("/", "overview-totals")
        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["overview-totals"]
        assert "Pages rendered" in envelope.html_for_zone("overview-totals")


class TestLazyLoadZone:
    """The busiest-pages widget ships a placeholder and loads its body lazily."""

    def test_overview_lazy_load_widget_renders_hint(self, client) -> None:
        body = client.get("/").content.decode()
        assert 'data-next-lazy="load"' in body
        assert 'data-next-zone="busiest-pages"' in body
        assert "Busiest pages" in body
        assert "Loading the busiest pages" in body

    def test_lazy_zone_get_delivers_the_body(self, client) -> None:
        client.get("/stats/pages/")
        response = client.get_zones("/", "busiest-pages")
        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["busiest-pages"]
        html = envelope.html_for_zone("busiest-pages")
        assert "stats/pages/page.py" in html
        assert "No pages have been rendered yet." not in html
        assert "Loading the busiest pages" not in html

    def test_lazy_zone_get_skips_the_totals_provider(self, client, monkeypatch) -> None:
        calls: list[str] = []

        def _record(kind: str) -> int:
            calls.append(kind)
            return 0

        monkeypatch.setattr(metrics, "total_for_kind", _record)
        client.get_zones("/", "busiest-pages")
        assert calls == []


class TestMetricPulseVerb:
    """A partial apply morphs the totals zone and emits the custom verb."""

    def test_partial_apply_morphs_zone_and_emits_metric_pulse(self, client) -> None:
        response = client.post_action(
            "window_filter_form",
            {"window": "1h"},
            origin="/stats/",
            partial=True,
            zones="live-totals",
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph", "metric-pulse"]
        assert envelope.zone_targets() == ["live-totals"]

    def test_metric_pulse_op_carries_window_and_selector(self, client) -> None:
        response = client.post_action(
            "window_filter_form",
            {"window": "1h"},
            origin="/stats/",
            partial=True,
            zones="live-totals",
        )
        envelope = envelope_of(response)
        pulse = next(op for op in envelope.ops if op["op"] == "metric-pulse")
        assert pulse["window"] == "1h"
        assert pulse["selector"] == "[data-metric-pulse-target]"

    def test_partial_apply_reaggregates_under_the_chosen_window(
        self, client, frozen_now
    ) -> None:
        with frozen_now("2026-05-08T12:00:00+00:00") as traveller:
            metrics.incr("pages.rendered", "/old", by=40)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            narrow = envelope_of(
                client.post_action(
                    "window_filter_form",
                    {"window": "1m"},
                    origin="/stats/",
                    partial=True,
                    zones="live-totals",
                )
            ).html_for_zone("live-totals")
            wide = envelope_of(
                client.post_action(
                    "window_filter_form",
                    {"window": "1h"},
                    origin="/stats/",
                    partial=True,
                    zones="live-totals",
                )
            ).html_for_zone("live-totals")
        assert "40" not in narrow
        assert "40" in wide

    def test_live_page_carries_the_pulse_target_and_handler(self, client) -> None:
        body = client.get("/stats/").content.decode()
        assert "data-metric-pulse-target" in body
        assert 'data-next-zone="live-totals"' in body
        assert "/static/next/stats.js" in body

    def test_pulse_handler_is_scoped_to_the_live_page(self, client) -> None:
        body = client.get("/stats/pages/").content.decode()
        assert "/static/next/stats.js" not in body
        assert "data-next-target" not in body


class TestSignalGroupsCovered:
    """A walk through the dashboard increments every signal group at least once."""

    def test_each_group_increments(self, client) -> None:
        """Lifecycle-only signals are provoked inside the recorder window.

        `settings_reloaded`, `provider_registered`, and `watch_specs_ready`
        never fire on a plain page render, so the walk triggers one of each.
        """
        recorder_signals = [
            sig for signals in GROUP_SAMPLES.values() for sig in signals
        ]
        with SignalRecorder(*recorder_signals) as recorder:
            next_framework_settings.reload()

            class _TestProbeProvider(RegisteredParameterProvider):
                """Throwaway provider just to fire `provider_registered`."""

                def can_handle(self, _param: object, _context: object) -> bool:
                    return False

                def resolve(self, _param: object, _context: object) -> None:
                    return None

            iter_all_autoreload_watch_specs()

            _walk_dashboard(client)
            client.post_action("window_filter_form", {"window": "5m"})

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
