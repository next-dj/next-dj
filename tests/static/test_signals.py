from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory, override_settings

from next.static import (
    AssetDiscovery,
    StaticAsset,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
)
from next.static.signals import (
    asset_registered,
    backend_loaded,
    collector_finalized,
    html_injected,
)
from next.testing import SignalRecorder, capture_signals
from tests.support import StaticAssetProvider


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from next.components import ComponentInfo


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


@pytest.fixture()
def capture_asset_registered() -> Generator[SignalRecorder, None, None]:
    """Record ``asset_registered`` emissions."""
    with capture_signals(asset_registered) as recorder:
        yield recorder


@pytest.fixture()
def capture_collector_finalized() -> Generator[SignalRecorder, None, None]:
    """Record ``collector_finalized`` emissions."""
    with capture_signals(collector_finalized) as recorder:
        yield recorder


@pytest.fixture()
def capture_html_injected() -> Generator[SignalRecorder, None, None]:
    """Record ``html_injected`` emissions."""
    with capture_signals(html_injected) as recorder:
        yield recorder


@pytest.fixture()
def capture_backend_loaded() -> Generator[SignalRecorder, None, None]:
    """Record ``backend_loaded`` emissions."""
    with capture_signals(backend_loaded) as recorder:
        yield recorder


class TestAssetRegisteredSignal:
    def test_fired_from_discovery(
        self,
        tmp_path: Path,
        file_backend: StaticFilesBackend,
        capture_asset_registered: SignalRecorder,
    ) -> None:

        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        collector = StaticCollector()
        AssetDiscovery(provider).discover_page_assets(page_path, collector)

        assert len(capture_asset_registered) == 1
        event = capture_asset_registered.events[0]
        assert isinstance(event.sender, StaticAsset)
        assert event.kwargs["collector"] is collector
        assert event.kwargs["backend"] is file_backend

    def test_a_warm_page_render_fires_the_same_set_again(
        self,
        tmp_path: Path,
        file_backend: StaticFilesBackend,
        capture_asset_registered: SignalRecorder,
    ) -> None:
        """A warm render re-emits per file asset, and never for a module URL."""
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.py"
        page_path.write_text('scripts = ["https://cdn.example.com/x.js"]\n')

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        discovery.discover_page_assets(page_path, StaticCollector())
        warm = StaticCollector()
        discovery.discover_page_assets(page_path, warm)

        assert len(capture_asset_registered) == 2
        cold_event, warm_event = capture_asset_registered.events
        assert warm_event.sender == cold_event.sender
        assert warm_event.kwargs["collector"] is warm
        assert warm_event.kwargs["backend"] is file_backend

    def test_a_warm_component_render_fires_the_same_set_again(
        self,
        file_backend: StaticFilesBackend,
        composite_component: ComponentInfo,
        capture_asset_registered: SignalRecorder,
    ) -> None:
        discovery = AssetDiscovery(StaticAssetProvider(file_backend))
        discovery.discover_component_assets(composite_component, StaticCollector())
        warm = StaticCollector()
        discovery.discover_component_assets(composite_component, warm)

        assert len(capture_asset_registered) == 4
        warmed = capture_asset_registered.events[2:]
        assert [e.kwargs["collector"] for e in warmed] == [warm, warm]
        assert [e.sender for e in warmed] == [
            e.sender for e in capture_asset_registered.events[:2]
        ]


class TestCollectorFinalizedSignal:
    def test_fired_on_inject_with_page_path(
        self,
        tmp_path: Path,
        fresh_manager: StaticManager,
        capture_collector_finalized: SignalRecorder,
    ) -> None:
        collector = StaticCollector()
        page_path = tmp_path / "page.djx"
        fresh_manager.inject("<body/>", collector, page_path=page_path)

        assert len(capture_collector_finalized) == 1
        assert capture_collector_finalized.events[0].sender is collector
        assert capture_collector_finalized.events[0].kwargs["page_path"] == page_path

    def test_page_path_is_optional(
        self, fresh_manager: StaticManager, capture_collector_finalized: SignalRecorder
    ) -> None:
        collector = StaticCollector()
        fresh_manager.inject("<body/>", collector)

        assert len(capture_collector_finalized) == 1
        assert capture_collector_finalized.events[0].kwargs["page_path"] is None
        assert capture_collector_finalized.events[0].kwargs["request"] is None

    def test_carries_request_when_provided(
        self, fresh_manager: StaticManager, capture_collector_finalized: SignalRecorder
    ) -> None:
        collector = StaticCollector()
        sentinel = RequestFactory().get("/")
        fresh_manager.inject("<body/>", collector, request=sentinel)

        assert capture_collector_finalized.events[0].kwargs["request"] is sentinel


class TestHtmlInjectedSignal:
    def test_fired_with_before_and_after(
        self, fresh_manager: StaticManager, capture_html_injected: SignalRecorder
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, collector)

        assert len(capture_html_injected) == 1
        event = capture_html_injected.events[0]
        assert event.sender is fresh_manager
        assert event.kwargs["html_before"] == html
        assert event.kwargs["html_after"] == out
        assert event.kwargs["collector"] is collector
        assert event.kwargs["placeholders_replaced"] == ("styles",)
        assert event.kwargs["injected_bytes"] == len(out) - len(html)

    def test_reports_no_placeholders_when_html_has_none(
        self, fresh_manager: StaticManager, capture_html_injected: SignalRecorder
    ) -> None:
        collector = StaticCollector()
        fresh_manager.inject("<body/>", collector)
        assert capture_html_injected.events[0].kwargs["placeholders_replaced"] == ()

    def test_reports_both_placeholders_when_html_has_both(
        self, fresh_manager: StaticManager, capture_html_injected: SignalRecorder
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
        collector.add(StaticAsset(url="https://cdn/a.js", kind="js"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"
        fresh_manager.inject(html, collector)
        assert capture_html_injected.events[0].kwargs["placeholders_replaced"] == (
            "styles",
            "scripts",
        )

    def test_carries_request_when_provided(
        self, fresh_manager: StaticManager, capture_html_injected: SignalRecorder
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
        sentinel = RequestFactory().get("/")
        fresh_manager.inject(
            f"<head>{STYLES_PLACEHOLDER}</head>", collector, request=sentinel
        )
        assert capture_html_injected.events[0].kwargs["request"] is sentinel


class TestBackendLoadedSignal:
    def test_fired_for_each_configured_backend(
        self, fresh_manager: StaticManager, capture_backend_loaded: SignalRecorder
    ) -> None:
        config = {"BACKEND": "next.static.StaticFilesBackend", "OPTIONS": {}}
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": [config]}):
            fresh_manager._ensure_backends()

        assert len(capture_backend_loaded) == 1
        event = capture_backend_loaded.events[0]
        assert event.sender is StaticFilesBackend
        assert event.kwargs["instance"] is fresh_manager.default_backend
        assert event.kwargs["config"] == config

    def test_sender_class_allows_filtering(
        self, fresh_manager: StaticManager, capture_backend_loaded: SignalRecorder
    ) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend"}]
            }
        ):
            fresh_manager._ensure_backends()
        senders = [e.sender for e in capture_backend_loaded]
        assert all(s is StaticFilesBackend for s in senders)

    def test_seeded_fallback_announces_itself(
        self, fresh_manager: StaticManager, capture_backend_loaded: SignalRecorder
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": []}):
            fresh_manager._ensure_backends()

        assert isinstance(fresh_manager.default_backend, StaticFilesBackend)
        assert [event.sender for event in capture_backend_loaded] == [
            StaticFilesBackend
        ]

    def test_seed_after_a_skipped_entry_announces_itself(
        self, fresh_manager: StaticManager, capture_backend_loaded: SignalRecorder
    ) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            fresh_manager._ensure_backends()

        assert isinstance(fresh_manager.default_backend, StaticFilesBackend)
        assert [event.kwargs["instance"] for event in capture_backend_loaded] == [
            fresh_manager.default_backend
        ]


class TestSignalsAreDjangoSignals:
    def test_asset_registered_has_connect(self) -> None:
        assert hasattr(asset_registered, "connect")

    def test_collector_finalized_has_disconnect(self) -> None:
        assert hasattr(collector_finalized, "disconnect")

    def test_html_injected_has_send(self) -> None:
        assert hasattr(html_injected, "send")

    def test_backend_loaded_has_send(self) -> None:
        assert hasattr(backend_loaded, "send")
