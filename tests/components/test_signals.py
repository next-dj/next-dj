from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from django.dispatch import Signal

from next.components import ComponentInfo, render_component
from next.components.backends import DummyBackend
from next.components.manager import ComponentsManager
from next.components.registry import ComponentRegistry
from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)
from next.testing import SignalRecorder


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
        self, capture_component_registered: SignalRecorder
    ) -> None:
        """A connected listener receives kwargs from ``.send``."""
        component_registered.send(sender=object, name="card")
        assert len(capture_component_registered) == 1
        assert capture_component_registered.events[0].kwargs["name"] == "card"

    def test_sender_is_preserved(
        self, capture_component_registered: SignalRecorder
    ) -> None:
        """``sender`` passed to ``.send`` is captured on the event."""

        class _Fake:
            pass

        component_registered.send(sender=_Fake, name="hero")
        assert capture_component_registered.events[0].sender is _Fake

    def test_disconnect_stops_receiving(
        self, capture_component_registered: SignalRecorder
    ) -> None:
        """After fixture teardown the listener is removed (no cross-test bleed)."""
        component_registered.send(sender=object, name="x")
        assert len(capture_component_registered) == 1

    def test_registry_register_emits_per_component(
        self,
        component_info_factory: Callable[..., ComponentInfo],
        capture_component_registered: SignalRecorder,
    ) -> None:
        """`ComponentRegistry.register` fires once per component with `info`."""
        registry = ComponentRegistry()
        info_a = component_info_factory(name="a", template_name="a.djx")
        info_b = component_info_factory(name="b", template_name="b.djx")
        registry.register(info_a)
        registry.register(info_b)
        assert len(capture_component_registered) == 2
        infos = [ev.kwargs["info"] for ev in capture_component_registered]
        assert infos == [info_a, info_b]
        senders = {ev.sender for ev in capture_component_registered}
        assert senders == {ComponentRegistry}

    def test_registry_register_many_skips_singular_signal(
        self,
        component_info_factory: Callable[..., ComponentInfo],
        capture_component_registered: SignalRecorder,
    ) -> None:
        """`register_many` does not fire the per-item `component_registered`."""
        items = [
            component_info_factory(name=f"c{i}", template_name=f"c{i}.djx")
            for i in range(3)
        ]
        ComponentRegistry().register_many(items)
        assert len(capture_component_registered) == 0


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
        self, capture_components_registered: SignalRecorder
    ) -> None:
        """A connected listener receives `infos` from `.send`."""
        components_registered.send(sender=object, infos=())
        assert len(capture_components_registered) == 1
        assert capture_components_registered.events[0].kwargs["infos"] == ()

    def test_registry_register_many_emits_one_batch_event(
        self, items: list[ComponentInfo], capture_components_registered: SignalRecorder
    ) -> None:
        """`register_many` fires `components_registered` exactly once."""
        ComponentRegistry().register_many(items)
        assert len(capture_components_registered) == 1
        event = capture_components_registered.events[0]
        assert event.sender is ComponentRegistry
        assert event.kwargs["infos"] == tuple(items)

    def test_registry_register_many_empty_does_not_fire(
        self, capture_components_registered: SignalRecorder
    ) -> None:
        """An empty bulk call stays silent."""
        ComponentRegistry().register_many([])
        assert len(capture_components_registered) == 0

    def test_registry_register_singular_path_does_not_fire_batch(
        self,
        component_info_factory: Callable[..., ComponentInfo],
        capture_components_registered: SignalRecorder,
    ) -> None:
        """The singular `register` does not fire `components_registered`."""
        ComponentRegistry().register(
            component_info_factory(name="solo", template_name="solo.djx")
        )
        assert len(capture_components_registered) == 0


class TestComponentBackendLoadedSignal:
    """``component_backend_loaded`` wiring."""

    def test_listener_receives_manual_send(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """A connected listener receives kwargs from ``.send``."""
        component_backend_loaded.send(
            sender=object, config={"BACKEND": "next.components.FileComponentsBackend"}
        )
        assert len(capture_component_backend_loaded) == 1
        assert capture_component_backend_loaded.events[0].kwargs["config"] == {
            "BACKEND": "next.components.FileComponentsBackend"
        }

    def test_sender_is_preserved(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """``sender`` is echoed back from the event."""

        class _Backend:
            pass

        component_backend_loaded.send(sender=_Backend)
        assert capture_component_backend_loaded.events[0].sender is _Backend

    def test_manager_reload_emits_per_backend(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """`ComponentsManager.reload` fires once per built backend."""
        manager = ComponentsManager()
        configs = [
            {"BACKEND": "next.components.DummyBackend", "COMPONENTS_DIR": "a"},
            {"BACKEND": "next.components.DummyBackend", "COMPONENTS_DIR": "b"},
        ]

        with patch("next.backends.next_framework_settings") as fake_settings:
            fake_settings.COMPONENT_BACKENDS = configs
            manager.reload()

        assert len(capture_component_backend_loaded) == 2
        captured_configs = [
            ev.kwargs["config"] for ev in capture_component_backend_loaded
        ]
        assert captured_configs == configs

    def test_sender_is_the_backend_class(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """The class is the sender, so receivers can filter on it."""
        manager = ComponentsManager()
        with patch("next.backends.next_framework_settings") as fake_settings:
            fake_settings.COMPONENT_BACKENDS = [
                {"BACKEND": "next.components.DummyBackend"}
            ]
            manager.reload()

        senders = {ev.sender for ev in capture_component_backend_loaded}
        assert senders == {DummyBackend}

    def test_instance_carries_the_loaded_backend(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """``instance`` is the backend the manager kept, under its new name."""
        manager = ComponentsManager()
        with patch("next.backends.next_framework_settings") as fake_settings:
            fake_settings.COMPONENT_BACKENDS = [
                {"BACKEND": "next.components.DummyBackend"}
            ]
            manager.reload()

        event = capture_component_backend_loaded.events[0]
        assert event.kwargs["instance"] is manager._backends[0]
        assert "backend" not in event.kwargs

    def test_config_is_a_copy_of_the_entry(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """A receiver mutating ``config`` cannot corrupt the settings entry."""
        entry = {"BACKEND": "next.components.DummyBackend"}
        manager = ComponentsManager()
        with patch("next.backends.next_framework_settings") as fake_settings:
            fake_settings.COMPONENT_BACKENDS = [entry]
            manager.reload()

        captured = capture_component_backend_loaded.events[0].kwargs["config"]
        assert captured == entry
        assert captured is not entry

    def test_skipped_entry_sends_nothing(
        self, capture_component_backend_loaded: SignalRecorder
    ) -> None:
        """An entry that never loads emits no event."""
        manager = ComponentsManager()
        with patch("next.backends.next_framework_settings") as fake_settings:
            fake_settings.COMPONENT_BACKENDS = [
                {"BACKEND": "next.components.NoSuchBackend"}
            ]
            manager.reload()

        assert len(capture_component_backend_loaded) == 0
        assert manager._backends == []


class TestComponentRenderedSignal:
    """``component_rendered`` wiring."""

    def test_listener_receives_manual_send(
        self, capture_component_rendered: SignalRecorder
    ) -> None:
        """A connected listener receives kwargs from ``.send``."""
        component_rendered.send(sender=object, name="card", html="<div>card</div>")
        assert len(capture_component_rendered) == 1
        assert capture_component_rendered.events[0].kwargs["html"] == "<div>card</div>"

    def test_sender_is_preserved(
        self, capture_component_rendered: SignalRecorder
    ) -> None:
        """``sender`` is echoed back from the event."""

        class _Renderer:
            pass

        component_rendered.send(sender=_Renderer, html="<b/>")
        assert capture_component_rendered.events[0].sender is _Renderer

    def test_multiple_listeners_all_notified(
        self, capture_component_rendered: SignalRecorder
    ) -> None:
        """Two calls produce two events."""
        component_rendered.send(sender=object, html="<a/>")
        component_rendered.send(sender=object, html="<b/>")
        assert len(capture_component_rendered) == 2

    def test_render_component_emits_when_listener_connected(
        self, tmp_path: Path, capture_component_rendered: SignalRecorder
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
        event = capture_component_rendered.events[0]
        assert event.kwargs["info"] is info
        assert event.kwargs["template_path"] == template_path

    def test_render_component_skips_send_without_listeners(
        self, tmp_path: Path
    ) -> None:
        """``render_component`` does not dispatch when no listener is connected."""
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
        with patch.object(component_rendered, "send") as send:
            html = render_component(info, {"title": "Hello"})
        assert "Hello" in html
        send.assert_not_called()
