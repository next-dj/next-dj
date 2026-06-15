import pytest

from next.partial.headers import CONTENT_TYPE
from next.testing import NextClient, envelope_of


@pytest.fixture()
def client() -> NextClient:
    """Test client that submits form fields manually, without CSRF checks."""
    return NextClient(enforce_csrf_checks=False)


class TestInvalidPartialEnvelope:
    """An invalid partial POST returns one extract-morph of the failed form."""

    def test_status_and_headers(self, client: NextClient) -> None:
        response = client.post_action(
            "regression_form", {"name": ""}, origin="/", partial=True
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        assert response["X-Next-Form"] == "invalid"
        assert response["X-Next-Action"]

    def test_single_extract_morph_of_the_form(self, client: NextClient) -> None:
        response = client.post_action(
            "regression_form", {"name": ""}, origin="/", partial=True
        )
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        uid = response["X-Next-Action"]
        assert envelope.form_targets() == [uid]
        assert envelope.ops[0].get("extract") is True

    def test_form_meta_carries_machine_errors(self, client: NextClient) -> None:
        response = client.post_action(
            "regression_form", {"name": ""}, origin="/", partial=True
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert meta["valid"] is False
        assert meta["errors"]["name"] == ["This field is required."]


class TestSuccessFunnelEnvelope:
    """A valid partial POST whose handler returns None morphs the form."""

    def test_success_returns_envelope(self, client: NextClient) -> None:
        response = client.post_action(
            "regression_form", {"name": "ok"}, origin="/", partial=True
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        assert envelope_of(response).op_verbs() == ["morph"]

    def test_success_omits_invalid_headers(self, client: NextClient) -> None:
        response = client.post_action(
            "regression_form", {"name": "ok"}, origin="/", partial=True
        )
        assert "X-Next-Form" not in response


class TestWizardAdvanceShapingPending:
    """Advance shaping is stubbed until the next wizard step lands."""

    @pytest.mark.skip(reason="advance shaping pending")
    def test_advance_emits_step_zone_morph(self) -> None:
        msg = "advance shaping renders the next wizard step zone"
        raise NotImplementedError(msg)
