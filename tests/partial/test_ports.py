import pytest
from django.http import HttpResponse
from django.test import Client

from next.partial.headers import VARY_HEADERS
from next.partial.ports import PartialShaperImpl
from next.ports import PartialShaper
from tests.support import IntentOnlyShaper, parameter_shape, port_methods


def _varied(response) -> set[str]:
    """Return the Vary field names of a response, lowercased."""
    return {name.strip().lower() for name in response.get("Vary", "").split(",")}


_PARTIAL_VARY = {name.lower() for name in VARY_HEADERS}


class TestPageResponseVary:
    """A page served through the shaper port varies on the partial headers.

    Both page views hand their response to the port rather than stamping Vary
    themselves, so the assertion is on what a shared cache would key the page by.
    """

    def test_static_body_page_varies_on_the_partial_headers(self) -> None:
        assert _varied(Client().get("/")) >= _PARTIAL_VARY

    def test_dynamic_body_page_varies_on_the_partial_headers(self) -> None:
        assert _varied(Client().get("/dynamic/")) >= _PARTIAL_VARY

    def test_a_page_short_circuiting_with_a_redirect_is_left_alone(self) -> None:
        # A redirect is not a cacheable 2xx response, so the port leaves its Vary alone.
        response = Client().get("/redirecting/")
        assert response.status_code == 302
        assert _PARTIAL_VARY.isdisjoint(_varied(response))


class TestPartialShaperPort:
    """Both implementations keep the exact call shape the port declares.

    mypy never reads `tests/`, so the stub the intent-gate tests bind is checked here.
    """

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert port_methods(PartialShaper) == [
            "intent",
            "set_vary",
            "shape_response",
            "shape_validate",
            "zone_response",
        ]

    @pytest.mark.parametrize("name", port_methods(PartialShaper))
    @pytest.mark.parametrize(
        "implementation", [PartialShaperImpl, IntentOnlyShaper], ids=["real", "stub"]
    )
    def test_implementation_parameters_match_the_port(
        self, implementation, name
    ) -> None:
        assert parameter_shape(implementation, name) == parameter_shape(
            PartialShaper, name
        )

    def test_set_vary_declares_the_partial_request_headers(self) -> None:
        """Without these headers a shared cache could serve a partial body as a page."""
        response = HttpResponse("<p>ok</p>")

        PartialShaperImpl().set_vary(response)

        assert "X-Next-Request" in response.headers["Vary"]
        assert "X-Next-Zone" in response.headers["Vary"]
