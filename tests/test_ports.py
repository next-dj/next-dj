import inspect

import pytest

from next.partial.shaper import PartialShaperImpl
from next.ports import PartialShaper, PartialShaperSlot, partial_shaper_slot
from tests.support import IntentOnlyShaper


def _port_methods() -> list[str]:
    return sorted(
        name
        for name, member in vars(PartialShaper).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


def _parameter_shape(owner: type, name: str) -> list[tuple[str, object, object]]:
    signature = inspect.signature(getattr(owner, name))
    return [
        (param.name, param.kind, param.default)
        for param in signature.parameters.values()
    ]


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(RuntimeError, match="unbound"):
            PartialShaperSlot().get()


class TestBoundSlot:
    """A bound slot answers the very object it was given."""

    def test_get_returns_the_bound_object(self) -> None:
        slot = PartialShaperSlot()
        shaper = IntentOnlyShaper()
        slot.set(shaper)
        assert slot.get() is shaper

    def test_set_replaces_the_previous_binding(self) -> None:
        slot = PartialShaperSlot()
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


class TestAppComposition:
    """`AppConfig.ready` leaves the process-wide slot bound to the real shaper."""

    def test_process_slot_holds_the_partial_implementation(self) -> None:
        assert isinstance(partial_shaper_slot.get(), PartialShaperImpl)
