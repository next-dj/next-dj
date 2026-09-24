from django.test import Client

from next.partial.headers import VARY_HEADERS


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
        # the page returns its own response before the port is ever reached, so
        # stamping Vary on it would be the port reaching past its own seam
        response = Client().get("/redirecting/")
        assert response.status_code == 302
        assert _PARTIAL_VARY.isdisjoint(_varied(response))
