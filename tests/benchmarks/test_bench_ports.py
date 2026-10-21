from __future__ import annotations

import pytest

from next.partial.headers import partial_intent
from next.ports import partial_shaper_slot
from tests.support import plain_get


class TestBenchShaperGate:
    """What a full render pays to ask the port whether it must shape.

    The direct `partial_intent` read beside the gate prices the port hop itself.
    """

    @pytest.mark.benchmark(group="ports.shaper_gate")
    def test_gate_through_the_port(self, benchmark) -> None:
        request = plain_get("/zoned/")

        def gate() -> bool:
            shaper = partial_shaper_slot.get()
            return bool(shaper.intent(request).zones)

        assert gate() is False
        benchmark(gate)

    @pytest.mark.benchmark(group="ports.shaper_gate")
    def test_gate_without_the_port(self, benchmark) -> None:
        request = plain_get("/zoned/")

        def gate() -> bool:
            return bool(partial_intent(request).zones)

        assert gate() is False
        benchmark(gate)

    @pytest.mark.benchmark(group="ports.shaper_gate")
    def test_slot_lookup(self, benchmark) -> None:
        benchmark(partial_shaper_slot.get)
