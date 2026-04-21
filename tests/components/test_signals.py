from collections.abc import Generator
from typing import Any

import pytest

from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
)


@pytest.fixture()
def capture_component_registered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_registered`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    component_registered.connect(_listener)
    try:
        yield events
    finally:
        component_registered.disconnect(_listener)


@pytest.fixture()
def capture_component_backend_loaded() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_backend_loaded`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    component_backend_loaded.connect(_listener)
    try:
        yield events
    finally:
        component_backend_loaded.disconnect(_listener)


@pytest.fixture()
def capture_component_rendered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_rendered`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    component_rendered.connect(_listener)
    try:
        yield events
    finally:
        component_rendered.disconnect(_listener)


class TestComponentSignalsAreDjangoSignals:
    """All three component signals expose the Django ``Signal`` interface."""

    def test_component_registered_has_connect(self) -> None:
        """``component_registered`` exposes ``.connect``."""
        assert hasattr(component_registered, "connect")

    def test_component_registered_has_disconnect(self) -> None:
        """``component_registered`` exposes ``.disconnect``."""
        assert hasattr(component_registered, "disconnect")

    def test_component_registered_has_send(self) -> None:
        """``component_registered`` exposes ``.send``."""
        assert hasattr(component_registered, "send")

    def test_component_backend_loaded_has_connect(self) -> None:
        """``component_backend_loaded`` exposes ``.connect``."""
        assert hasattr(component_backend_loaded, "connect")

    def test_component_backend_loaded_has_disconnect(self) -> None:
        """``component_backend_loaded`` exposes ``.disconnect``."""
        assert hasattr(component_backend_loaded, "disconnect")

    def test_component_backend_loaded_has_send(self) -> None:
        """``component_backend_loaded`` exposes ``.send``."""
        assert hasattr(component_backend_loaded, "send")

    def test_component_rendered_has_connect(self) -> None:
        """``component_rendered`` exposes ``.connect``."""
        assert hasattr(component_rendered, "connect")

    def test_component_rendered_has_disconnect(self) -> None:
        """``component_rendered`` exposes ``.disconnect``."""
        assert hasattr(component_rendered, "disconnect")

    def test_component_rendered_has_send(self) -> None:
        """``component_rendered`` exposes ``.send``."""
        assert hasattr(component_rendered, "send")


class TestComponentRegisteredSignal:
    """``component_registered`` wiring."""

    def test_listener_receives_manual_send(
        self,
        capture_component_registered: list[dict[str, Any]],
    ) -> None:
        """A connected listener receives kwargs from ``.send``."""
        component_registered.send(
            sender=object,
            name="card",
        )
        assert len(capture_component_registered) == 1
        assert capture_component_registered[0]["name"] == "card"

    def test_sender_is_preserved(
        self,
        capture_component_registered: list[dict[str, Any]],
    ) -> None:
        """``sender`` passed to ``.send`` is captured in the event dict."""

        class _Fake:
            pass

        component_registered.send(sender=_Fake, name="hero")
        assert capture_component_registered[0]["sender"] is _Fake

    def test_disconnect_stops_receiving(
        self,
        capture_component_registered: list[dict[str, Any]],
    ) -> None:
        """After fixture teardown the listener is removed (no cross-test bleed)."""
        component_registered.send(sender=object, name="x")
        assert len(capture_component_registered) == 1


class TestComponentBackendLoadedSignal:
    """``component_backend_loaded`` wiring."""

    def test_listener_receives_manual_send(
        self,
        capture_component_backend_loaded: list[dict[str, Any]],
    ) -> None:
        """A connected listener receives kwargs from ``.send``."""
        component_backend_loaded.send(
            sender=object,
            config={"BACKEND": "next.components.FileComponentsBackend"},
        )
        assert len(capture_component_backend_loaded) == 1
        assert "config" in capture_component_backend_loaded[0]

    def test_sender_is_preserved(
        self,
        capture_component_backend_loaded: list[dict[str, Any]],
    ) -> None:
        """``sender`` is echoed back from the event."""

        class _Backend:
            pass

        component_backend_loaded.send(sender=_Backend)
        assert capture_component_backend_loaded[0]["sender"] is _Backend


class TestComponentRenderedSignal:
    """``component_rendered`` wiring."""

    def test_listener_receives_manual_send(
        self,
        capture_component_rendered: list[dict[str, Any]],
    ) -> None:
        """A connected listener receives kwargs from ``.send``."""
        component_rendered.send(
            sender=object,
            name="card",
            html="<div>card</div>",
        )
        assert len(capture_component_rendered) == 1
        assert capture_component_rendered[0]["html"] == "<div>card</div>"

    def test_sender_is_preserved(
        self,
        capture_component_rendered: list[dict[str, Any]],
    ) -> None:
        """``sender`` is echoed back from the event."""

        class _Renderer:
            pass

        component_rendered.send(sender=_Renderer, html="<b/>")
        assert capture_component_rendered[0]["sender"] is _Renderer

    def test_multiple_listeners_all_notified(
        self,
        capture_component_rendered: list[dict[str, Any]],
    ) -> None:
        """Two calls produce two events."""
        component_rendered.send(sender=object, html="<a/>")
        component_rendered.send(sender=object, html="<b/>")
        assert len(capture_component_rendered) == 2
