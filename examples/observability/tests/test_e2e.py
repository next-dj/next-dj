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
    """The `live_stats` key carries through `window.Next.context` after override."""

    def test_window_next_context_contains_live_stats(self, client) -> None:
        response = client.get("/stats/")
        body = response.content.decode()
        assert '"live_stats":' in body
        assert '"render_rates":' in body
        assert '"bars":' in body

    def test_window_querystring_propagates_to_inherit_context(self, client) -> None:
        response = client.get("/stats/?window=1h")
        body = response.content.decode()
        assert "Window: 1h" in body


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

    def test_flush_command_is_idempotent_when_empty(self) -> None:
        assert metrics.read_all() == {}
        call_command("flush_metrics")
        assert MetricSnapshot.objects.count() == 0
