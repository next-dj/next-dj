from __future__ import annotations

import pytest
from django.http import HttpResponse

from next.ports import (
    deferrals_slot,
    marked_zones,
    render_deferred,
    router_stamp_slot,
    stamp_router,
)
from next.router.navigation import action_marking, stamp
from tests.support import plain_get


def _rendered() -> str:
    return "<p>body</p>"


class TestBenchPortHop:
    """What a request pays to reach an area above it through a port slot.

    The direct call beside each hop prices the port itself.
    """

    @pytest.mark.benchmark(group="ports.hop")
    def test_slot_lookup(self, benchmark) -> None:
        assert benchmark(router_stamp_slot.get) is stamp

    @pytest.mark.benchmark(group="ports.hop")
    def test_slot_peek(self, benchmark) -> None:
        assert benchmark(deferrals_slot.peek) is not None

    @pytest.mark.benchmark(group="ports.hop")
    def test_router_stamp_through_the_port(self, benchmark) -> None:
        """The stamp every form and action response takes, with no directive to set."""
        request = plain_get("/zoned/")
        response = HttpResponse("ok")
        assert benchmark(stamp_router, response, request) is response

    @pytest.mark.benchmark(group="ports.hop")
    def test_router_stamp_without_the_port(self, benchmark) -> None:
        request = plain_get("/zoned/")
        response = HttpResponse("ok")
        assert benchmark(stamp, response, request) is response

    @pytest.mark.benchmark(group="ports.hop")
    def test_zone_marking_through_the_port(self, benchmark) -> None:
        """The active-link block an action dispatch opens around its zone renders."""
        request = plain_get("/zoned/")

        def marked() -> None:
            with marked_zones(request):
                pass

        benchmark(marked)

    @pytest.mark.benchmark(group="ports.hop")
    def test_zone_marking_without_the_port(self, benchmark) -> None:
        request = plain_get("/zoned/")

        def marked() -> None:
            with action_marking(request):
                pass

        benchmark(marked)

    @pytest.mark.benchmark(group="ports.hop")
    def test_deferral_sink_with_nothing_deferred(self, benchmark) -> None:
        """A render that queues no deferred call, settled through the bound sink."""
        request = plain_get("/zoned/")

        def settled() -> str:
            return render_deferred(_rendered, collector=None, request=request)

        assert benchmark(settled) == "<p>body</p>"
