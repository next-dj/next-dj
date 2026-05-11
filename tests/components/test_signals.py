from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.dispatch import Signal

from next.components import ComponentInfo, render_component
from next.components.backends import ComponentsBackend, ComponentsFactory
from next.components.manager import ComponentsManager
from next.components.registry import ComponentRegistry
from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)


@pytest.mark.parametrize(
    "signal",
    [
        pytest.param(component_registered, id="component_registered"),
        pytest.param(components_registered, id="components_registered"),
        pytest.param(component_backend_loaded, id="component_backend_loaded"),
        pytest.param(component_rendered, id="component_rendered"),
    ],
)
@pytest.mark.parametrize("method", ["connect", "disconnect", "send"])
class TestComponentSignalsAreDjangoSignals:
    """Every component signal exposes the Django :class:`Signal` interface."""

    def test_exposes_method(self, signal: Signal, method: str) -> None:
        """The signal carries the named Django dispatch method."""
        assert hasattr(signal, method)


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

    def test_registry_register_emits_per_component(
        self,
        component_info_factory: Callable[..., ComponentInfo],
        capture_component_registered: list[dict[str, Any]],
    ) -> None:
        """`ComponentRegistry.register` fires once per component with `info`."""
        registry = ComponentRegistry()
        info_a = component_info_factory(name="a", template_name="a.djx")
        info_b = component_info_factory(name="b", template_name="b.djx")
        registry.register(info_a)
        registry.register(info_b)
        assert len(capture_component_registered) == 2
        infos = [ev["info"] for ev in capture_component_registered]
        assert infos == [info_a, info_b]
        senders = {ev["sender"] for ev in capture_component_registered}
        assert senders == {ComponentRegistry}

    def test_registry_register_many_skips_singular_signal(
        self,
        component_info_factory: Callable[..., ComponentInfo],
        capture_component_registered: list[dict[str, Any]],
    ) -> None:
        """`register_many` does not fire the per-item `component_registered`."""
        items = [
            component_info_factory(name=f"c{i}", template_name=f"c{i}.djx")
            for i in range(3)
        ]
        ComponentRegistry().register_many(items)
        assert capture_component_registered == []


class TestComponentsRegisteredSignal:
    """`components_registered` (plural) wiring for bulk registration."""

    @pytest.fixture()
    def items(
        self, component_info_factory: Callable[..., ComponentInfo]
    ) -> list[ComponentInfo]:
        """Three named `ComponentInfo` objects for batch-registration cases."""
        return [
            component_info_factory(name=f"c{i}", template_name=f"c{i}.djx")
            for i in range(3)
        ]

    def test_listener_receives_manual_send(
        self,
        capture_components_registered: list[dict[str, Any]],
    ) -> None:
        """A connected listener receives `infos` from `.send`."""
        components_registered.send(sender=object, infos=())
        assert len(capture_components_registered) == 1
        assert capture_components_registered[0]["infos"] == ()

    def test_registry_register_many_emits_one_batch_event(
        self,
        items: list[ComponentInfo],
        capture_components_registered: list[dict[str, Any]],
    ) -> None:
        """`register_many` fires `components_registered` exactly once."""
        ComponentRegistry().register_many(items)
        assert len(capture_components_registered) == 1
        event = capture_components_registered[0]
        assert event["sender"] is ComponentRegistry
        assert event["infos"] == tuple(items)

    def test_registry_register_many_empty_does_not_fire(
        self,
        capture_components_registered: list[dict[str, Any]],
    ) -> None:
        """An empty bulk call stays silent."""
        ComponentRegistry().register_many([])
        assert capture_components_registered == []

    def test_registry_register_singular_path_does_not_fire_batch(
        self,
        component_info_factory: Callable[..., ComponentInfo],
        capture_components_registered: list[dict[str, Any]],
    ) -> None:
        """The singular `register` does not fire `components_registered`."""
        ComponentRegistry().register(
            component_info_factory(name="solo", template_name="solo.djx")
        )
        assert capture_components_registered == []


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

    def test_manager_reload_config_emits_per_backend(
        self,
        capture_component_backend_loaded: list[dict[str, Any]],
    ) -> None:
        """`ComponentsManager._reload_config` fires once per built backend."""
        sentinel: ComponentsBackend = ComponentsFactory.create_backend(
            {
                "BACKEND": "next.components.DummyBackend",
                "COMPONENTS_DIR": "_widgets",
            }
        )

        class _StubFactory:
            calls = 0

            @classmethod
            def create_backend(cls, _config: dict[str, Any]) -> ComponentsBackend:
                cls.calls += 1
                return sentinel

        manager = ComponentsManager()
        configs = [
            {"BACKEND": "next.components.DummyBackend", "COMPONENTS_DIR": "a"},
            {"BACKEND": "next.components.DummyBackend", "COMPONENTS_DIR": "b"},
        ]

        with (
            patch(
                "next.components.manager.next_framework_settings",
            ) as fake_settings,
            patch.object(
                ComponentsFactory, "create_backend", _StubFactory.create_backend
            ),
        ):
            fake_settings.DEFAULT_COMPONENT_BACKENDS = configs
            manager._reload_config()

        assert len(capture_component_backend_loaded) == 2
        senders = {ev["sender"] for ev in capture_component_backend_loaded}
        assert senders == {ComponentsManager}
        captured_configs = [ev["config"] for ev in capture_component_backend_loaded]
        assert captured_configs == configs
        assert all(ev["backend"] is sentinel for ev in capture_component_backend_loaded)


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

    def test_render_component_emits_when_listener_connected(
        self,
        tmp_path: Path,
        capture_component_rendered: list[dict[str, Any]],
    ) -> None:
        """``render_component`` fires ``component_rendered`` when a listener exists."""
        template_path = tmp_path / "card.djx"
        template_path.write_text("<h3>{{ title }}</h3>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=template_path,
            module_path=None,
            is_simple=True,
        )
        render_component(info, {"title": "Hello"})
        assert len(capture_component_rendered) == 1
        event = capture_component_rendered[0]
        assert event["info"] is info
        assert event["template_path"] == template_path
