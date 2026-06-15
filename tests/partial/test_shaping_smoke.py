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


@pytest.mark.django_db()
class TestWizardAdvanceMorphsTheNextStepZone:
    """A partial step advance morphs the master zone of the next step.

    The wizard page declares the `wizard-zone` master. A valid first step
    advances without a redirect, the second wizard binds to the next step
    page and renders the unbound scope form into a zone morph.
    """

    def test_advance_returns_a_zone_morph_envelope(self, client: NextClient) -> None:
        response = client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["wizard-zone"]

    def test_advance_renders_the_next_step_form(self, client: NextClient) -> None:
        response = client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        html = envelope_of(response).html_for_zone("wizard-zone")
        assert 'name="scope"' in html
        assert 'name="name"' not in html

    def test_default_advance_pushes_no_history(self, client: NextClient) -> None:
        response = client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert "url" not in envelope_of(response).op_verbs()


@pytest.mark.django_db()
class TestWizardAdvancePushesHistoryWhenOptedIn:
    """A wizard with `Meta.push_steps` adds a `url.push` to the advance."""

    def test_advance_pushes_the_next_step_url(self, client: NextClient) -> None:
        response = client.post_action(
            "push_step_wizard",
            {"name": "Ada"},
            origin="/wizard_push/identity/",
            partial=True,
            zones="push-zone",
        )
        ops = envelope_of(response).ops
        url_ops = [op for op in ops if op["op"] == "url"]
        assert url_ops == [
            {"op": "url", "action": "push", "href": "/wizard_push/scope/"}
        ]
