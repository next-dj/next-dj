from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from django.dispatch import Signal

from next.conf import next_framework_settings
from next.urls import RouterManager, router_manager
from next.urls.signals import route_registered, router_reloaded


@pytest.fixture()
def capture_route_registered() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    route_registered.connect(_listener)
    try:
        yield events
    finally:
        route_registered.disconnect(_listener)


@pytest.fixture()
def capture_router_reloaded() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    router_reloaded.connect(_listener)
    try:
        yield events
    finally:
        router_reloaded.disconnect(_listener)


class TestRouteRegisteredSignal:
    """``route_registered`` signal can be connected to and receives kwargs."""

    def test_signal_is_importable(self) -> None:
        """``route_registered`` is a Django Signal exported from ``next.urls.signals``."""
        assert isinstance(route_registered, Signal)

    def test_listener_receives_sent_event(
        self, capture_route_registered: list[dict[str, Any]]
    ) -> None:
        """Manually sending ``route_registered`` notifies connected listeners."""
        route_registered.send(sender=object, url_path="test/path")
        assert len(capture_route_registered) == 1

    def test_sender_is_passed_through(
        self, capture_route_registered: list[dict[str, Any]]
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        route_registered.send(sender=sentinel, url_path="home")
        assert capture_route_registered[0]["sender"] is sentinel

    def test_kwargs_are_passed_through(
        self, capture_route_registered: list[dict[str, Any]]
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        route_registered.send(sender=object, url_path="about", name="page_about")
        assert capture_route_registered[0]["url_path"] == "about"
        assert capture_route_registered[0]["name"] == "page_about"

    def test_multiple_sends_accumulate(
        self, capture_route_registered: list[dict[str, Any]]
    ) -> None:
        """Each send appends a new event to the captured list."""
        route_registered.send(sender=object, url_path="a")
        route_registered.send(sender=object, url_path="b")
        assert len(capture_route_registered) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs: object) -> None:
            events.append({"sender": sender})

        route_registered.connect(_listener)
        route_registered.disconnect(_listener)
        route_registered.send(sender=object)
        assert len(events) == 0


class TestRouterReloadedSignal:
    """``router_reloaded`` signal can be connected to and receives kwargs."""

    def test_signal_is_importable(self) -> None:
        """``router_reloaded`` is a Django Signal exported from ``next.urls.signals``."""
        assert isinstance(router_reloaded, Signal)

    def test_listener_receives_sent_event(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """Manually sending ``router_reloaded`` notifies connected listeners."""
        router_reloaded.send(sender=object)
        assert len(capture_router_reloaded) == 1

    def test_sender_is_passed_through(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        router_reloaded.send(sender=sentinel)
        assert capture_router_reloaded[0]["sender"] is sentinel

    def test_kwargs_are_passed_through(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        router_reloaded.send(sender=object, backend_count=3)
        assert capture_router_reloaded[0]["backend_count"] == 3

    def test_multiple_sends_accumulate(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """Each send appends a new event to the captured list."""
        router_reloaded.send(sender=object)
        router_reloaded.send(sender=object)
        assert len(capture_router_reloaded) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs: object) -> None:
            events.append({"sender": sender})

        router_reloaded.connect(_listener)
        router_reloaded.disconnect(_listener)
        router_reloaded.send(sender=object)
        assert len(events) == 0


class TestRouterManagerReloadIntegration:
    """`RouterManager.reload` emits `router_reloaded` and clears URL caches."""

    def test_reload_emits_router_reloaded_once(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """Each `reload` call publishes exactly one `router_reloaded` event."""
        manager = RouterManager()
        manager.reload()
        assert len(capture_router_reloaded) == 1

    def test_reload_sender_is_router_manager_class(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """The `sender` of a manager-driven event is the `RouterManager` class."""
        manager = RouterManager()
        manager.reload()
        assert capture_router_reloaded[0]["sender"] is RouterManager

    def test_reload_clears_django_url_cache(self) -> None:
        """`reload` invalidates Django URL resolver caches for fresh dispatch."""
        with patch("next.urls.manager.clear_url_caches") as mock_clear:
            RouterManager().reload()
            mock_clear.assert_called_once()

    def test_settings_reload_chain_publishes_event(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """`next_framework_settings.reload` triggers exactly one router event."""
        capture_router_reloaded.clear()
        next_framework_settings.reload()
        assert len(capture_router_reloaded) >= 1
        senders = {event["sender"] for event in capture_router_reloaded}
        assert RouterManager in senders

    def test_global_router_manager_reload_is_idempotent(
        self, capture_router_reloaded: list[dict[str, Any]]
    ) -> None:
        """Two consecutive `reload` calls publish two events with stable state."""
        router_manager.reload()
        router_manager.reload()
        assert len(capture_router_reloaded) >= 2
