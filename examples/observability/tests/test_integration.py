import re
from io import StringIO

import pytest
from config.storages import MANIFEST_STORAGES
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from obs import metrics
from obs.metrics import PREFIX, CounterLostError
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
    form_backend_loaded,
    form_validation_failed,
    wizard_backend_loaded,
)
from next.pages.signals import context_registered, page_rendered, template_loaded
from next.partial.signals import partial_backend_loaded
from next.router import CutKind
from next.server import iter_all_autoreload_watch_specs
from next.server.signals import watch_specs_ready
from next.static import get_static_manager
from next.static.signals import (
    asset_registered,
    collector_finalized,
    html_injected,
    static_backend_loaded,
)
from next.testing import (
    SignalRecorder,
    assert_metadata,
    boundary_errors,
    envelope_of,
    find_anchor,
    init_payload,
)
from next.urls.signals import route_registered, router_backend_loaded, router_reloaded


GROUP_SAMPLES: dict[str, list] = {
    "conf": [settings_reloaded],
    "deps": [provider_registered],
    "pages": [template_loaded, context_registered, page_rendered],
    "urls": [route_registered, router_reloaded],
    "components": [component_registered, components_registered, component_rendered],
    "forms": [action_registered, action_dispatched, form_validation_failed],
    "static": [asset_registered, collector_finalized, html_injected],
    "server": [watch_specs_ready],
    "backends": [
        component_backend_loaded,
        form_backend_loaded,
        partial_backend_loaded,
        router_backend_loaded,
        static_backend_loaded,
        wizard_backend_loaded,
    ],
}


pytestmark = pytest.mark.django_db


CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

HASHED_CHART_THEME = re.compile(r"/static/dashboards/js/chart_theme\.[0-9a-f]+\.js")
HASHED_RENDER_CHART = re.compile(r"/static/next/components/render_chart\.[0-9a-f]+\.js")


@pytest.fixture()
def manifest_storage(tmp_path):
    with override_settings(
        STATIC_ROOT=tmp_path / "collected", STORAGES=MANIFEST_STORAGES
    ):
        call_command("collectstatic", interactive=False, verbosity=0)
        yield tmp_path / "collected"


DASHBOARD_PATHS: tuple[str, ...] = (
    "/",
    "/stats/",
    "/stats/?window=1m",
    "/stats/pages/",
    "/stats/components/",
    "/stats/forms/",
    "/stats/static/",
)


_ZONE_ATTR = re.compile(r'data-next-zone="([^"]+)"')


def _walk_dashboard(next_client) -> None:
    """Hit every observability page so receivers accumulate counters."""
    for url in DASHBOARD_PATHS:
        response = next_client.get(url)
        assert response.status_code == 200


@pytest.fixture()
def apply_window(next_client):
    """Submit the window filter as a partial aimed at the live totals zone."""

    def apply(window: str):
        return next_client.post_action(
            "window_filter_form",
            {"window": window},
            origin="/stats/",
            partial="live-totals",
        )

    return apply


class TestOverview:
    """Overview page exposes headline counters that match metric reads."""

    def test_overview_renders_with_zero_counters(self, next_client) -> None:
        response = next_client.get("/")
        assert response.status_code == 200
        assert b"Pages rendered" in response.content
        assert b"Components rendered" in response.content
        assert b"Actions dispatched" in response.content

    def test_overview_counters_advance_after_warmup(self, next_client) -> None:
        next_client.get("/stats/")
        next_client.get("/stats/pages/")
        response = next_client.get("/")
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
        ids=(
            "live_distribution",
            "per_page_counts",
            "per_component_counts",
            "form_actions",
            "static_pipeline",
        ),
    )
    def test_subpage(self, next_client, url, needle) -> None:
        response = next_client.get(url)
        assert response.status_code == 200
        assert needle.encode() in response.content


class TestNavMarks:
    """The header nav and the stats tabs are marked for the page on screen."""

    def test_the_visited_tab_is_the_current_page(self, next_client) -> None:
        body = next_client.get("/stats/pages/").content.decode()
        assert 'aria-current="page"' in find_anchor(
            body, href="/stats/pages/", text="Pages"
        )
        assert 'data-next-current="ancestor"' in find_anchor(
            body, href="/stats/", text="Live"
        )
        assert "data-next-current" not in find_anchor(
            body, href="/stats/forms/", text="Forms"
        )

    def test_the_header_marks_the_section_and_spares_home(self, next_client) -> None:
        body = next_client.get("/stats/pages/").content.decode()
        assert 'data-next-current="ancestor"' in find_anchor(
            body, href="/stats/", text="Stats"
        )
        assert "data-next-current" not in find_anchor(body, href="/", text="Overview")


class TestLiveStatsSerializerOverride:
    """The override on `live_stats` wraps the payload in a versioned envelope.

    Sibling keys `render_rates` and `totals_chart` stay flat, proving per-key control.
    """

    def test_live_stats_carries_envelope(self, next_client) -> None:
        response = next_client.get("/stats/")
        body = response.content.decode()
        assert '"live_stats":{"v":1,"data":{' in body

    def test_render_rates_stays_flat_through_global_serializer(
        self, next_client
    ) -> None:
        """`render_chart` declares no `serializer=`, so its key lands flat."""
        response = next_client.get("/stats/")
        body = response.content.decode()
        assert '"render_rates":{' in body
        assert '"render_rates":{"v":1' not in body

    def test_overview_totals_chart_carries_envelope(self, next_client) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert '"totals_chart":{"v":1,"data":{' in body

    @pytest.mark.parametrize(
        "path",
        ["/stats/", "/stats/pages/", "/stats/static/"],
        ids=["declaring-page", "pages-tab", "static-tab"],
    )
    def test_window_querystring_propagates_to_inherit_context(
        self, next_client, path: str
    ) -> None:
        body = next_client.get(f"{path}?window=1h").content.decode()
        assert "Window: 1h" in body


class TestWindowFilters:
    """`?window=...` actually narrows the aggregation through bucket reads."""

    BASE = "2026-05-08T12:00:00+00:00"

    def test_only_recent_buckets_count_under_one_minute_window(
        self, next_client, frozen_now
    ) -> None:
        """A bucket 30 minutes old falls outside the one-minute read window."""
        with frozen_now(self.BASE) as traveller:
            metrics.incr("pages.rendered", "/old", by=99)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            metrics.incr("pages.rendered", "/recent", by=2)
            response = next_client.get("/stats/?window=1m")
            body = response.content.decode()
            payload = init_payload(body)
            assert payload["live_stats"]["data"]["window"] == "1m"
            assert payload["render_rates"]["window"] == "1m"
            recent = metrics.read_window("pages.rendered", minutes=1)
            assert "/old" not in recent
            assert recent.get("/recent", 0) >= 2

    def test_wider_window_reaches_older_buckets(self, next_client, frozen_now) -> None:
        with frozen_now(self.BASE) as traveller:
            metrics.incr("pages.rendered", "/old", by=99)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            next_client.get("/stats/?window=1h")
            wide = metrics.read_window("pages.rendered", minutes=60)
            assert wide["/old"] == 99


class TestJsxAssetPipeline:
    """`.jsx` files are emitted as `<script type="text/babel">` tags."""

    def test_overview_emits_babel_script_tag(self, next_client) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert '<script type="text/babel"' in body
        assert "sparkline" in body

    def test_overview_loads_react_and_babel_cdn_scripts(self, next_client) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert "react@18" in body
        assert "babel/standalone" in body

    def test_stats_keeps_chart_js_on_regular_script_path(self, next_client) -> None:
        response = next_client.get("/stats/")
        body = response.content.decode()
        assert "chart.umd.min.js" in body
        assert body.index("chart.umd.min.js") < body.index(
            "/static/next/components/render_chart.js"
        )


class TestStaticNameResolution:
    """A page `scripts` list mixes staticfiles names with ready vendor URLs."""

    def test_name_resolves_through_staticfiles(self, next_client) -> None:
        body = next_client.get("/stats/").content.decode()
        assert '<script src="/static/dashboards/js/chart_theme.js">' in body

    def test_vendor_url_is_left_alone(self, next_client) -> None:
        body = next_client.get("/stats/").content.decode()
        assert f'<script src="{CHART_JS_CDN}">' in body

    def test_theme_lands_between_the_vendor_url_and_the_widget_file(
        self, next_client
    ) -> None:
        body = next_client.get("/stats/").content.decode()
        assert (
            body.index(CHART_JS_CDN)
            < body.index("/static/dashboards/js/chart_theme.js")
            < body.index("/static/next/components/render_chart.js")
        )


class TestManifestStorage:
    """The `OBS_STATIC_MANIFEST=1` profile hashes every name through the manifest."""

    def test_page_script_name_carries_a_content_hash(
        self, next_client, manifest_storage
    ) -> None:
        body = next_client.get("/stats/").content.decode()
        assert HASHED_CHART_THEME.search(body) is not None
        assert '"/static/dashboards/js/chart_theme.js"' not in body

    def test_vendor_url_in_the_same_list_stays_unhashed(
        self, next_client, manifest_storage
    ) -> None:
        body = next_client.get("/stats/").content.decode()
        assert f'<script src="{CHART_JS_CDN}">' in body

    def test_colocated_widget_file_is_hashed_too(
        self, next_client, manifest_storage
    ) -> None:
        body = next_client.get("/stats/").content.decode()
        assert HASHED_RENDER_CHART.search(body) is not None

    def test_hardcoded_path_misses_the_manifest(self, manifest_storage) -> None:
        manager = get_static_manager()
        literal = "/static/dashboards/js/chart_theme.js"
        assert manager.resolve_url(literal) == literal
        resolved = manager.resolve_url("dashboards/js/chart_theme.js")
        assert HASHED_CHART_THEME.fullmatch(resolved) is not None

    def test_collectstatic_leaves_every_python_module_behind(
        self, manifest_storage
    ) -> None:
        leaked = [
            entry.name
            for entry in manifest_storage.rglob("*")
            if entry.suffix in {".py", ".pyc"} or entry.name == "__pycache__"
        ]
        assert leaked == []


class TestDevFlagChannel:
    """The framework owns `$dev` in the init payload and gates it on DEBUG."""

    @override_settings(DEBUG=True)
    def test_debug_render_announces_the_dev_flag(self, next_client) -> None:
        payload = init_payload(next_client.get("/").content.decode())
        assert payload["$dev"] is True

    @override_settings(DEBUG=False)
    def test_production_render_omits_the_dev_flag(self, next_client) -> None:
        payload = init_payload(next_client.get("/").content.decode())
        assert "$dev" not in payload
        assert "$csrf" in payload

    @override_settings(DEBUG=True)
    def test_dashboard_context_keys_travel_beside_the_reserved_ones(
        self, next_client
    ) -> None:
        payload = init_payload(next_client.get("/stats/").content.decode())
        assert "live_stats" in payload
        assert payload["$dev"] is True


class TestSparklineStaysOutsideEveryZone:
    """No zone on the dashboard renders the sparkline mount.

    The `next.W074` silencing in `config/settings.py` holds only while that stays true.
    """

    def test_full_render_owns_the_sparkline(self, next_client) -> None:
        assert "sparkline-mount" in next_client.get("/").content.decode()

    def test_no_zone_body_carries_the_sparkline_mount(self, next_client) -> None:
        checked: list[str] = []
        for url in DASHBOARD_PATHS:
            body = next_client.get(url).content.decode()
            for zone in sorted(set(_ZONE_ATTR.findall(body))):
                html = envelope_of(next_client.get_zones(url, zone)).html_for_zone(zone)
                assert "sparkline-mount" not in html
                checked.append(zone)
        assert set(checked) >= {"overview-totals", "live-totals"}


class TestFilterFormDispatch:
    """Submitting the filter form fires `action_dispatched` and redirects."""

    def test_select_renders_with_seeded_window(self, next_client) -> None:
        body = next_client.get("/stats/?window=1h").content.decode()
        assert '<select name="window"' in body
        assert 'value="1h" selected' in body

    def test_post_redirects_with_window_querystring(self, next_client) -> None:
        with SignalRecorder(action_dispatched) as recorder:
            response = next_client.post_action(
                "window_filter_form", {"window": "1m"}, origin="/stats/"
            )
        assert response.status_code == 302
        assert "window=1m" in response["Location"]
        events = recorder.events_for(action_dispatched)
        assert len(events) == 1
        assert events[0].kwargs["response_status"] == 302

    def test_partial_post_without_the_zone_visits_the_live_page(
        self, next_client
    ) -> None:
        """A stats sub-page renders the filter zoneless, so its apply navigates."""
        response = next_client.post_action(
            "window_filter_form", {"window": "1h"}, origin="/stats/pages/", partial=True
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["visit"]
        assert envelope.ops[0]["href"] == "/stats/?window=1h"


class TestPollZone:
    """The overview totals zone carries a poll interval and still morphs."""

    def test_overview_zone_carries_poll_interval(self, next_client) -> None:
        body = next_client.get("/").content.decode()
        assert 'data-next-poll="5000"' in body
        assert 'data-next-zone="overview-totals"' in body

    def test_overview_zone_get_still_morphs(self, next_client) -> None:
        response = next_client.get_zones("/", "overview-totals")
        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["overview-totals"]
        assert "Pages rendered" in envelope.html_for_zone("overview-totals")


class TestBusiestPagesOnLoad:
    """The busiest-pages widget ships its loading UI and fetches itself on load."""

    def test_first_paint_ships_the_loading_ui(self, next_client) -> None:
        body = next_client.get("/").content.decode()
        assert 'data-next-defer="load"' in body
        assert "Busiest pages" in body
        assert "Loading the busiest pages" in body
        assert "No pages have been rendered yet." not in body

    def test_the_boundary_request_delivers_the_list(self, next_client) -> None:
        next_client.get("/stats/pages/")
        envelope = next_client.get_boundary("/", "busiest_pages")
        [op] = envelope.ops
        assert op["s"] == "ready"
        assert "stats/pages/page.py" in op["html"]
        assert "Loading the busiest pages" not in op["html"]

    def test_a_failed_request_offers_a_retry(self, next_client, monkeypatch) -> None:
        def _lost(kind: str, *, limit: int | None = None) -> list[tuple[str, int]]:
            raise CounterLostError(kind, "obs/pages/page.py")

        monkeypatch.setattr(metrics, "top_by_kind", _lost)
        with boundary_errors("render"):
            envelope = next_client.get_boundary("/", "busiest_pages")
        [op] = envelope.ops
        assert op["s"] == "error"
        assert "The busiest pages are unavailable" in op["html"]
        assert "data-next-retry" in op["html"]

    def test_the_boundary_request_skips_the_totals_provider(
        self, next_client, monkeypatch
    ) -> None:
        calls: list[str] = []

        def _record(kind: str) -> int:
            calls.append(kind)
            return 0

        monkeypatch.setattr(metrics, "total_for_kind", _record)
        next_client.get_boundary("/", "busiest_pages")
        assert calls == []


CULLED_PAGE = "obs/pages/stats/page.py"


@pytest.fixture()
def culled_render_count():
    """Leave a duration total whose render count the cache already culled."""
    metrics.incr("pages.rendered", CULLED_PAGE)
    metrics.incr("pages.duration_ms_total", CULLED_PAGE, by=40)
    cache.delete(f"{PREFIX}pages.rendered:{CULLED_PAGE}")
    return CULLED_PAGE


class TestLatencyWidget:
    """The latency widget fails alone, its `error.djx` standing in for it."""

    def test_widget_starts_with_its_empty_message(self, next_client) -> None:
        body = next_client.get("/").content.decode()
        assert "Render latency" in body
        assert "No render times have been recorded yet." in body

    def test_widget_lists_the_mean_render_time_per_page(self, next_client) -> None:
        next_client.get("/stats/")
        response = next_client.get("/")
        body = response.content.decode()
        assert "Mean ms" in body
        assert CULLED_PAGE in body
        assert response.boundary_failures == ()

    @pytest.mark.usefixtures("culled_render_count")
    def test_a_culled_counter_replaces_the_widget_alone(self, next_client) -> None:
        with boundary_errors("render"):
            response = next_client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert 'data-next-state="error" data-next-error="error"' in body
        assert "Render latency is unavailable" in body
        assert "Mean ms" not in body
        assert "Pages rendered" in body
        assert "sparkline-mount" in body
        assert "Loading the busiest pages" in body

    @pytest.mark.usefixtures("culled_render_count")
    def test_the_failure_is_recorded_with_its_digest(self, next_client) -> None:
        with boundary_errors("render"):
            response = next_client.get("/")
        [failure] = response.boundary_failures
        assert (failure.name, failure.addressing, failure.kind) == (
            "latency_widget",
            "component",
            "error",
        )
        assert isinstance(failure.exception, CounterLostError)
        reference = f'Reference <code class="font-mono">{failure.digest}</code>'
        assert reference in response.content.decode()

    @pytest.mark.usefixtures("culled_render_count", "rendered_boundary_errors")
    def test_the_degraded_page_stays_out_of_every_cache(self, next_client) -> None:
        response = next_client.get("/")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"

    @pytest.mark.usefixtures("culled_render_count")
    def test_without_the_opt_in_the_client_reraises(self, next_client) -> None:
        with pytest.raises(CounterLostError):
            next_client.get("/")


LATENCY_LOADING = "Averaging the render times"


def navigation_html(navigation) -> str:
    return "".join(op.get("html") or "" for op in navigation.envelope.ops)


class TestLatencyStream:
    """The latency widget streams behind its `loading.djx` and settles in a chunk."""

    def test_the_shell_ships_the_loading_ui_and_a_chunk_settles_it(
        self, next_client
    ) -> None:
        next_client.get("/stats/")
        streamed = next_client.get_stream("/")
        assert LATENCY_LOADING in streamed.shell
        assert "Mean ms" not in streamed.shell
        [chunk] = streamed.chunks
        assert chunk.state == "ready"
        assert CULLED_PAGE in chunk.html

    @pytest.mark.parametrize(
        ("sent", "shown", "hidden"),
        [
            ({"prefetch": True}, LATENCY_LOADING, "Mean ms"),
            ({"headers": {"X-Next-Prefetch": "full"}}, "Mean ms", LATENCY_LOADING),
        ],
        ids=("shell", "full"),
    )
    def test_a_prefetch_depth_decides_whether_the_job_runs(
        self, next_client, sent, shown, hidden
    ) -> None:
        next_client.get("/")
        navigation = next_client.navigate("/", origin="/stats/", **sent)
        html = navigation_html(navigation)
        assert shown in html
        assert hidden not in html


PAGE_RENDERS = "/stats/pages/"


class TestPageRendersBoundary:
    """The page renders table is a page boundary, streamed behind its `loading.djx`."""

    def test_the_shell_keeps_the_layout_and_shows_the_skeleton(
        self, next_client
    ) -> None:
        streamed = next_client.get_stream(PAGE_RENDERS)
        assert "data-page-renders-skeleton" in streamed.shell
        assert find_anchor(streamed.shell, href="/stats/pages/", text="Pages")
        assert "Per-page render counts" not in streamed.shell

    def test_a_chunk_fills_in_the_counts_and_the_means(self, next_client) -> None:
        next_client.get("/stats/")
        streamed = next_client.get_stream(PAGE_RENDERS)
        [chunk] = streamed.chunks
        assert chunk.state == "ready"
        assert "Mean ms" in chunk.html
        assert CULLED_PAGE in chunk.html

    def test_a_render_count_without_a_duration_shows_no_mean(self, next_client) -> None:
        metrics.incr("pages.rendered", CULLED_PAGE)
        body = next_client.get(PAGE_RENDERS).content.decode()
        assert CULLED_PAGE in body
        assert "&mdash;" in body

    @pytest.mark.usefixtures("culled_render_count")
    def test_a_culled_counter_streams_the_error_ui_in_place(self, next_client) -> None:
        with boundary_errors("render"):
            streamed = next_client.get_stream(PAGE_RENDERS)
        [chunk] = streamed.chunks
        assert (chunk.state, chunk.kind) == ("error", "error")
        assert "Page renders are unavailable" in chunk.html
        assert streamed.response.status_code == 200

    @pytest.mark.usefixtures("culled_render_count")
    def test_an_inline_page_keeps_its_layout_and_answers_500(self, next_client) -> None:
        with boundary_errors("render"):
            response = next_client.get(PAGE_RENDERS)
        body = response.content.decode()
        assert response.status_code == 500
        assert "Page renders are unavailable" in body
        assert find_anchor(body, href="/stats/", text="Live")
        [failure] = response.boundary_failures
        assert (failure.name, failure.addressing) == (
            "obs/pages/stats/pages/loading.djx",
            "page",
        )
        assert isinstance(failure.exception, CounterLostError)


class TestFlushHistory:
    """The flush history waits behind a button until the visitor asks for it."""

    def test_the_first_paint_ships_the_button_alone(self, next_client) -> None:
        body = next_client.get("/").content.decode()
        assert 'data-next-defer="manual"' in body
        assert "Show the flush history" in body
        assert "data-flush-history" not in body

    def test_the_fetch_says_when_nothing_was_flushed(self, next_client) -> None:
        envelope = next_client.get_boundary("/", "flush_history")
        [op] = envelope.ops
        assert op["s"] == "ready"
        assert "Nothing has been flushed yet." in op["html"]

    def test_the_fetch_lists_each_minute_a_flush_ran_in(self, next_client) -> None:
        next_client.get("/stats/")
        call_command("flush_metrics", stdout=StringIO())
        rows = MetricSnapshot.objects.count()
        envelope = next_client.get_boundary("/", "flush_history")
        [op] = envelope.ops
        assert "data-flush-history" in op["html"]
        assert (
            f'<td class="py-2 pl-4 text-right tabular-nums">{rows}</td>' in op["html"]
        )


class TestMetricPulseVerb:
    """A partial apply morphs the totals zone and emits the custom verb."""

    def test_partial_apply_morphs_zone_and_emits_metric_pulse(
        self, apply_window
    ) -> None:
        response = apply_window("1h")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == [
            "morph",
            "context",
            "morph",
            "context",
            "metric-pulse",
        ]
        assert envelope.zone_targets() == ["live-totals", "stats-window"]

    def test_partial_apply_morphs_the_window_label_beside_the_totals(
        self, apply_window
    ) -> None:
        envelope = envelope_of(apply_window("1h"))
        assert "Window: 1h" in envelope.html_for_zone("stats-window")

    def test_metric_pulse_op_carries_window_and_selector(self, apply_window) -> None:
        envelope = envelope_of(apply_window("1h"))
        pulse = next(op for op in envelope.ops if op["op"] == "metric-pulse")
        assert pulse["window"] == "1h"
        assert pulse["selector"] == "[data-metric-pulse-target]"

    def test_partial_apply_reaggregates_under_the_chosen_window(
        self, apply_window, frozen_now
    ) -> None:
        with frozen_now("2026-05-08T12:00:00+00:00") as traveller:
            metrics.incr("pages.rendered", "/old", by=40)
            traveller.move_to("2026-05-08T12:30:00+00:00")
            narrow = envelope_of(apply_window("1m")).html_for_zone("live-totals")
            wide = envelope_of(apply_window("1h")).html_for_zone("live-totals")
        assert "40" not in narrow
        assert "40" in wide

    def test_live_page_carries_the_pulse_target_and_handler(self, next_client) -> None:
        body = next_client.get("/stats/").content.decode()
        assert "data-metric-pulse-target" in body
        assert 'data-next-zone="live-totals"' in body
        assert "/static/next/obs/pages/stats.js" in body

    def test_pulse_handler_is_scoped_to_the_live_page(self, next_client) -> None:
        body = next_client.get("/stats/pages/").content.decode()
        assert "/static/next/obs/pages/stats.js" not in body
        assert "data-next-target" not in body

    def test_leaving_the_live_page_keeps_the_layout_and_drops_the_target(
        self, next_client
    ) -> None:
        step = next_client.navigate("/stats/pages/", origin="/stats/")
        assert step.kind is CutKind.SLOT
        assert step.envelope is not None
        form = step.envelope.html_for_zone("stats-filter")
        assert 'name="window"' in form
        assert "data-next-target" not in form


class TestSignalGroupsCovered:
    """A walk through the dashboard increments every signal group at least once."""

    def test_each_group_increments(self, next_client) -> None:
        """Lifecycle-only signals are provoked inside the recorder window."""
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

            _walk_dashboard(next_client)
            next_client.post_action(
                "window_filter_form", {"window": "5m"}, origin="/stats/"
            )

        for group, signals in GROUP_SAMPLES.items():
            hits = sum(len(recorder.events_for(sig)) for sig in signals)
            assert hits >= 1, (
                f"signal group {group!r} did not increment after walk: {signals}"
            )


class TestFlushMetricsCommand:
    """`flush_metrics` drains the cache and writes one row per counter."""

    def test_flush_persists_counters_and_clears_cache(self, next_client) -> None:
        _walk_dashboard(next_client)
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

    def test_flush_command_announces_count(self, next_client, capsys) -> None:
        _walk_dashboard(next_client)
        before = len(metrics.read_all())
        call_command("flush_metrics")
        captured = capsys.readouterr()
        assert f"flushed {before} counters" in captured.out


class TestPageMetadata:
    """The root dict keeps the dashboard out of the index, each stats page names itself."""

    @pytest.mark.parametrize(
        ("url", "title"),
        [
            ("/", "next.dj — Observability dashboard"),
            ("/stats/", "Live stats · next.dj observability"),
            ("/stats/pages/", "Page renders · next.dj observability"),
            ("/stats/components/", "Component renders · next.dj observability"),
            ("/stats/forms/", "Form actions · next.dj observability"),
            ("/stats/static/", "Static pipeline · next.dj observability"),
        ],
        ids=["overview", "live", "pages", "components", "forms", "static"],
    )
    def test_each_page_carries_its_title_and_noindex(
        self, next_client, url: str, title: str
    ) -> None:
        assert_metadata(next_client.get(url), title=title, robots="noindex")
