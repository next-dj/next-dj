from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from next.static import (
    AssetDiscovery,
    StaticAsset,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    StaticsFactory,
)
from next.static.signals import (
    asset_registered,
    backend_loaded,
    collector_finalized,
    html_injected,
)


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


@pytest.fixture()
def capture_asset_registered() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    asset_registered.connect(_listener)
    try:
        yield events
    finally:
        asset_registered.disconnect(_listener)


@pytest.fixture()
def capture_collector_finalized() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    collector_finalized.connect(_listener)
    try:
        yield events
    finally:
        collector_finalized.disconnect(_listener)


@pytest.fixture()
def capture_html_injected() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    html_injected.connect(_listener)
    try:
        yield events
    finally:
        html_injected.disconnect(_listener)


@pytest.fixture()
def capture_backend_loaded() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    backend_loaded.connect(_listener)
    try:
        yield events
    finally:
        backend_loaded.disconnect(_listener)


class TestAssetRegisteredSignal:
    def test_fired_from_discovery(
        self,
        tmp_path: Path,
        file_backend: StaticFilesBackend,
        capture_asset_registered: list[dict[str, Any]],
    ) -> None:

        class _P:
            @property
            def default_backend(self) -> StaticFilesBackend:
                return file_backend

            def page_roots(self) -> tuple[Path, ...]:
                return (tmp_path.resolve(),)

        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")

        collector = StaticCollector()
        AssetDiscovery(_P()).discover_page_assets(page_path, collector)

        assert len(capture_asset_registered) == 1
        event = capture_asset_registered[0]
        assert isinstance(event["sender"], StaticAsset)
        assert event["collector"] is collector
        assert event["backend"] is file_backend


class TestCollectorFinalizedSignal:
    def test_fired_on_inject_with_page_path(
        self,
        tmp_path: Path,
        fresh_manager: StaticManager,
        capture_collector_finalized: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        page_path = tmp_path / "page.djx"
        fresh_manager.inject("<body/>", collector, page_path=page_path)

        assert len(capture_collector_finalized) == 1
        assert capture_collector_finalized[0]["sender"] is collector
        assert capture_collector_finalized[0]["page_path"] == page_path

    def test_page_path_is_optional(
        self,
        fresh_manager: StaticManager,
        capture_collector_finalized: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        fresh_manager.inject("<body/>", collector)

        assert len(capture_collector_finalized) == 1
        assert capture_collector_finalized[0]["page_path"] is None
        assert capture_collector_finalized[0]["request"] is None

    def test_carries_request_when_provided(
        self,
        fresh_manager: StaticManager,
        capture_collector_finalized: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        sentinel = object()
        fresh_manager.inject(
            "<body/>",
            collector,
            request=sentinel,  # type: ignore[arg-type]
        )

        assert capture_collector_finalized[0]["request"] is sentinel


class TestHtmlInjectedSignal:
    def test_fired_with_before_and_after(
        self,
        fresh_manager: StaticManager,
        capture_html_injected: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, collector)

        assert len(capture_html_injected) == 1
        event = capture_html_injected[0]
        assert event["sender"] is fresh_manager
        assert event["html_before"] == html
        assert event["html_after"] == out
        assert event["collector"] is collector
        assert event["placeholders_replaced"] == ("styles",)
        assert event["injected_bytes"] == len(out) - len(html)

    def test_reports_no_placeholders_when_html_has_none(
        self,
        fresh_manager: StaticManager,
        capture_html_injected: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        fresh_manager.inject("<body/>", collector)
        assert capture_html_injected[0]["placeholders_replaced"] == ()

    def test_reports_both_placeholders_when_html_has_both(
        self,
        fresh_manager: StaticManager,
        capture_html_injected: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
        collector.add(StaticAsset(url="https://cdn/a.js", kind="js"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"
        fresh_manager.inject(html, collector)
        assert capture_html_injected[0]["placeholders_replaced"] == (
            "styles",
            "scripts",
        )

    def test_carries_request_when_provided(
        self,
        fresh_manager: StaticManager,
        capture_html_injected: list[dict[str, Any]],
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
        sentinel = object()
        fresh_manager.inject(
            f"<head>{STYLES_PLACEHOLDER}</head>",
            collector,
            request=sentinel,  # type: ignore[arg-type]
        )
        assert capture_html_injected[0]["request"] is sentinel


class TestLegacyReceiverCompat:
    """Receivers written before the `request` payload was added still work."""

    def test_collector_finalized_accepts_legacy_receiver(
        self, fresh_manager: StaticManager
    ) -> None:
        """A receiver with `(sender, **kwargs)` consumes the new `request` kwarg."""
        seen: list[dict[str, object]] = []

        def legacy(sender: object, **kwargs: object) -> None:
            seen.append({"sender": sender, **kwargs})

        collector_finalized.connect(legacy)
        try:
            collector = StaticCollector()
            fresh_manager.inject("<body/>", collector, request=object())
        finally:
            collector_finalized.disconnect(legacy)

        assert len(seen) == 1
        assert "request" in seen[0]
        assert "page_path" in seen[0]

    def test_html_injected_accepts_legacy_receiver(
        self, fresh_manager: StaticManager
    ) -> None:
        """A receiver with `(sender, **kwargs)` consumes the new `request` kwarg."""
        seen: list[dict[str, object]] = []

        def legacy(sender: object, **kwargs: object) -> None:
            seen.append({"sender": sender, **kwargs})

        html_injected.connect(legacy)
        try:
            collector = StaticCollector()
            collector.add(StaticAsset(url="https://cdn/a.css", kind="css"))
            fresh_manager.inject(
                f"<head>{STYLES_PLACEHOLDER}</head>",
                collector,
                request=object(),
            )
        finally:
            html_injected.disconnect(legacy)

        assert len(seen) == 1
        assert "request" in seen[0]
        assert "html_before" in seen[0]
        assert "html_after" in seen[0]


class TestBackendLoadedSignal:
    def test_fired_from_factory(
        self, capture_backend_loaded: list[dict[str, Any]]
    ) -> None:
        config = {"BACKEND": "next.static.StaticFilesBackend", "OPTIONS": {}}
        backend = StaticsFactory.create_backend(config)

        assert len(capture_backend_loaded) == 1
        event = capture_backend_loaded[0]
        assert event["sender"] is StaticFilesBackend
        assert event["instance"] is backend
        assert event["config"] == config

    def test_sender_class_allows_filtering(
        self, capture_backend_loaded: list[dict[str, Any]]
    ) -> None:
        StaticsFactory.create_backend({"BACKEND": "next.static.StaticFilesBackend"})
        senders = [e["sender"] for e in capture_backend_loaded]
        assert all(s is StaticFilesBackend for s in senders)


class TestSignalsAreDjangoSignals:
    def test_asset_registered_has_connect(self) -> None:
        assert hasattr(asset_registered, "connect")

    def test_collector_finalized_has_disconnect(self) -> None:
        assert hasattr(collector_finalized, "disconnect")

    def test_html_injected_has_send(self) -> None:
        assert hasattr(html_injected, "send")

    def test_backend_loaded_has_send(self) -> None:
        assert hasattr(backend_loaded, "send")
