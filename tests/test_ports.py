import pytest

from next.partial.shaper import PartialShaperImpl
from next.ports import PartialShaperSlot, partial_shaper_slot
from tests.support import IntentOnlyShaper


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(RuntimeError, match="unbound"):
            PartialShaperSlot().get()

    def test_get_raises_after_reset(self) -> None:
        slot = PartialShaperSlot()
        slot.set(IntentOnlyShaper())
        slot.reset()
        with pytest.raises(RuntimeError, match="unbound"):
            slot.get()


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


class TestAppComposition:
    """`AppConfig.ready` leaves the process-wide slot bound to the real shaper."""

    def test_process_slot_holds_the_partial_implementation(self) -> None:
        assert isinstance(partial_shaper_slot.get(), PartialShaperImpl)
