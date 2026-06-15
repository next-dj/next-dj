import pytest
from django.test import Client

from next.testing import NextClient, PartialEnvelope, envelope_of
from tests.forms import actions


# `actions` registers the baseline form actions on import. Bind it so the
# module survives unused-import cleanup whatever the collection order is.
_BASELINE_ACTIONS = actions


class TestNextClient:
    """NextClient extends django.test.Client with form-action shortcuts."""

    def test_get_action_url_returns_known_url(self) -> None:
        client = NextClient()
        url = client.get_action_url("simple_form")
        assert "_next/form/" in url

    def test_post_action_dispatches_form(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form_redirect",
            {"name": "Carol"},
        )
        assert response.status_code in (200, 302)

    @pytest.mark.django_db()
    def test_post_action_origin_fills_hidden_field(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""}, origin="/")
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    def test_post_action_without_origin_keeps_protocol_raw(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""})
        assert response.status_code == 400

    @pytest.mark.django_db()
    def test_post_action_data_origin_wins_over_keyword(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form",
            {"name": "", "_next_form_origin": "/"},
            origin="/no/such/route/",
        )
        assert response.status_code == 200


class TestGetZones:
    """`get_zones` GETs a URL as a partial zone request."""

    def test_single_zone_returns_envelope(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["alpha"]

    def test_tuple_of_zones_batches(self) -> None:
        response = NextClient().get_zones("/zoned/", ("alpha", "beta"))
        assert envelope_of(response).zone_targets() == ["alpha", "beta"]

    def test_version_header_drives_conflict(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", version="stale")
        assert response.status_code == 409

    def test_extra_headers_forwarded(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", HTTP_X_CUSTOM="present")
        assert response.wsgi_request.headers["X-Custom"] == "present"


class TestEnvelopeHelpers:
    """The structural envelope view answers questions about ops and targets."""

    def test_envelope_of_rejects_non_envelope(self) -> None:
        response = Client().get("/zoned/")
        with pytest.raises(AssertionError):
            envelope_of(response)

    def test_version_op_verbs_and_targets(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", ("alpha", "beta")))
        assert isinstance(envelope, PartialEnvelope)
        assert envelope.version == "0"
        assert envelope.op_verbs() == ["morph", "morph"]
        assert envelope.targets() == [{"zone": "alpha"}, {"zone": "beta"}]

    def test_assets_manifest_lists_co_located_css(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        assert {"kind": "css", "url": "/static/next/zoned.css"} in envelope.assets

    def test_html_for_zone_returns_payload(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        assert envelope.html_for_zone("alpha").startswith('<div data-next-zone="alpha"')

    def test_html_for_zone_raises_for_missing_zone(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        with pytest.raises(AssertionError):
            envelope.html_for_zone("absent")
