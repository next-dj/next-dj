import inspect

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.partial.shaper import PartialShaperImpl
from next.ports import (
    PartialShaper,
    PortSlot,
    RouterAccess,
    StaticAssets,
    partial_shaper_slot,
    router_access_slot,
    static_assets_slot,
)
from next.static.manager import StaticManager, default_manager
from next.urls import FileRouterBackend, RouterManager
from next.urls.access import RouterAccessImpl
from tests.support import IntentOnlyShaper, file_router_config_entry


def _methods_of(port: type) -> list[str]:
    return sorted(
        name
        for name, member in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


def _port_methods() -> list[str]:
    return _methods_of(PartialShaper)


def _call_shape(owner: type, name: str) -> list[tuple[str, object]]:
    """Parameter names and kinds, with the defaults an implementation may add."""
    signature = inspect.signature(getattr(owner, name))
    return [(param.name, param.kind) for param in signature.parameters.values()]


def _parameter_shape(owner: type, name: str) -> list[tuple[str, object, object]]:
    signature = inspect.signature(getattr(owner, name))
    return [
        (param.name, param.kind, param.default)
        for param in signature.parameters.values()
    ]


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="unbound"):
            PortSlot("partial shaper").get()

    @pytest.mark.parametrize(
        "subject", ["partial shaper", "router access port", "static assets port"]
    )
    def test_the_message_names_the_subject_the_slot_was_built_with(
        self, subject
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=subject):
            PortSlot(subject).get()

    def test_a_slot_carries_no_instance_dictionary(self) -> None:
        """Three process-wide singletons, so the slot stays a two-field object."""
        assert not hasattr(PortSlot("partial shaper"), "__dict__")


class TestBoundSlot:
    """A bound slot answers the very object it was given."""

    def test_get_returns_the_bound_object(self) -> None:
        slot = PortSlot("partial shaper")
        shaper = IntentOnlyShaper()
        slot.set(shaper)
        assert slot.get() is shaper

    def test_set_replaces_the_previous_binding(self) -> None:
        slot = PortSlot("partial shaper")
        slot.set(IntentOnlyShaper())
        replacement = IntentOnlyShaper()
        slot.set(replacement)
        assert slot.get() is replacement


class TestPortImplementations:
    """Both implementations keep the exact call shape the port declares.

    mypy checks the real implementation but never reads `tests/`, so the
    stub the intent-gate tests bind is compared against the port here.
    """

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _port_methods() == [
            "intent",
            "shape_response",
            "shape_validate",
            "zone_response",
        ]

    @pytest.mark.parametrize("name", _port_methods())
    @pytest.mark.parametrize(
        "implementation", [PartialShaperImpl, IntentOnlyShaper], ids=["real", "stub"]
    )
    def test_implementation_parameters_match_the_port(
        self, implementation, name
    ) -> None:
        assert _parameter_shape(implementation, name) == _parameter_shape(
            PartialShaper, name
        )


class TestRouterAccessPort:
    """The router port builds exactly what the urls area would build itself."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(RouterAccess) == ["create_backend", "create_manager"]

    @pytest.mark.parametrize("name", ["create_backend", "create_manager"])
    def test_implementation_parameters_match_the_port(self, name) -> None:
        assert _call_shape(RouterAccessImpl, name) == _call_shape(RouterAccess, name)

    def test_create_backend_builds_the_router_the_entry_names(self, tmp_path) -> None:
        backend = RouterAccessImpl().create_backend(
            file_router_config_entry(pages_dir=tmp_path)
        )
        assert isinstance(backend, FileRouterBackend)

    def test_create_manager_builds_a_fresh_manager(self) -> None:
        access = RouterAccessImpl()
        first = access.create_manager()
        assert isinstance(first, RouterManager)
        assert access.create_manager() is not first


class TestStaticAssetsPort:
    """The static port matches the manager the render path used to import."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(StaticAssets) == [
            "create_collector",
            "discover_page_assets",
            "inject",
        ]

    @pytest.mark.parametrize(
        "name", ["create_collector", "discover_page_assets", "inject"]
    )
    def test_the_static_manager_matches_the_port(self, name) -> None:
        assert _call_shape(StaticManager, name) == _call_shape(StaticAssets, name)


class TestSubscriptedSlotSingletons:
    """A subscripted slot is the plain holder, so the three singletons behave alike."""

    @pytest.mark.parametrize(
        "slot",
        [
            pytest.param(partial_shaper_slot, id="partial_shaper"),
            pytest.param(router_access_slot, id="router_access"),
            pytest.param(static_assets_slot, id="static_assets"),
        ],
    )
    def test_the_process_slots_are_port_slots(self, slot) -> None:
        assert isinstance(slot, PortSlot)


class TestAppComposition:
    """`AppConfig.ready` leaves the process-wide slots bound to the real objects."""

    def test_process_slot_holds_the_partial_implementation(self) -> None:
        assert isinstance(partial_shaper_slot.get(), PartialShaperImpl)

    def test_process_slot_holds_the_router_implementation(self) -> None:
        assert isinstance(router_access_slot.get(), RouterAccessImpl)

    def test_process_slot_holds_the_lazy_static_handle(self) -> None:
        assert static_assets_slot.get() is default_manager
