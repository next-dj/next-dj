import pytest
from django.template.base import NodeList

from next.boundaries.failures import BoundaryFailure, ErrorKind
from next.boundaries.options import DeferMode
from next.components import tags as component_tags
from next.components.calls import DeferredRender
from next.components.tags import ComponentNode
from next.components.trip import TripInput
from tests.components.sites import HEAD, LOGIN, TRIP, ComponentSite


class TestDeferredRender:
    """A trip renderer names its token input and, after a failure, its retry."""

    def _renderer(self, site: ComponentSite, **kwargs: object):
        info = site.add("card", "C", module=TRIP)
        trip = TripInput(info, DeferMode.STREAM, {"x": 1})
        return component_tags.isolated_renderer(info, site.root, trip), kwargs

    @pytest.mark.parametrize(
        "failure",
        [
            BoundaryFailure(ErrorKind.ERROR, "d", None, retry=False),
            BoundaryFailure(ErrorKind.FORBIDDEN, "d", 403, retry=True),
        ],
    )
    def test_no_retry_without_a_script_or_after_a_refusal(
        self, site: ComponentSite, failure: BoundaryFailure
    ) -> None:
        renderer, _ = self._renderer(site)
        assert renderer.retry_input(failure) is None

    def test_a_stream_input_is_no_client_trip(self, site: ComponentSite) -> None:
        renderer, _ = self._renderer(site)
        assert renderer.trip_input() is None

    def test_a_rule_that_never_passed_offers_no_retry(
        self, site: ComponentSite
    ) -> None:
        info = site.add("card", "C", module=LOGIN)
        node = ComponentNode("card", {}, NodeList())
        renderer = DeferredRender(
            node, info, site.root, {}, granted=False, tag="div", trip=None
        )
        failure = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=True)
        assert renderer.retry_input(failure) is None

    def test_a_declared_tag_wraps_an_isolated_render(self, site: ComponentSite) -> None:
        module = HEAD + 'component.defer("load", props=("x",), tag="section")\n'
        info = site.add("card", "C", module=module)
        trip = TripInput(info, DeferMode.LOAD, {"x": 1})
        renderer = component_tags.isolated_renderer(info, site.root, trip)
        assert renderer._tag == "section"
        assert renderer.trip_input() is trip
