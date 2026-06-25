import pytest

from next.partial import shaping as shaping_module
from next.partial.headers import CONTENT_TYPE
from next.testing import NextClient, envelope_of
from tests.support import CountingWizardBackend


@pytest.mark.django_db()
class TestWizardAdvanceStepsByEnvelope:
    """A partial step advance morphs the next step's zone, never redirects.

    The first step validates, the master zone of the next step renders the
    unbound scope form, the `name` field of the prior step is gone and the
    `scope` field of the next step has arrived.
    """

    def test_partial_advance_is_an_envelope_not_a_redirect(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        assert envelope_of(response).zone_targets() == ["wizard-zone"]

    def test_partial_advance_swaps_the_step_fields(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        html = envelope_of(response).html_for_zone("wizard-zone")
        assert 'name="scope"' in html
        assert 'name="name"' not in html

    def test_advance_stamps_next_step_origin_in_zone_html(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        html = envelope_of(response).html_for_zone("wizard-zone")
        assert 'value="/wizard/scope/"' in html
        assert 'value="/wizard/identity/"' not in html

    def test_draft_lands_in_storage_on_the_partial_advance(
        self, next_client: NextClient
    ) -> None:
        next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        follow = next_client.post_action(
            "step_wizard",
            {"scope": ""},
            origin="/wizard/scope/",
            partial=True,
            zones="wizard-zone",
        )
        html = envelope_of(follow).html_for_zone("wizard-zone")
        assert 'name="scope"' in html


@pytest.mark.django_db()
class TestWizardAdvanceWithoutPartialSwitch:
    """A wizard POST without the partial switch keeps its plain 302 step redirect."""

    def test_advance_is_a_redirect_to_the_next_step(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "step_wizard", {"name": "Ada"}, origin="/wizard/identity/"
        )
        assert response.status_code == 302
        assert response["Location"] == "/wizard/scope/"

    def test_no_partial_response_headers_leak(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "step_wizard", {"name": "Ada"}, origin="/wizard/identity/"
        )
        assert response["Content-Type"] != CONTENT_TYPE
        assert "X-Next-Version" not in response


@pytest.mark.django_db()
class TestWizardAdvanceRendersZoneNotPageView:
    """The advance renders only the next step zone, never the step page view.

    The advance builds the next wizard and renders its master zone through
    `render_zone`, so the step page view never runs and authorization stays
    in the action guard. The zone render fires exactly once, for the next
    step's page and zone.
    """

    def test_advance_renders_one_zone_and_no_page_view(
        self, next_client: NextClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple] = []
        original = shaping_module.render_zone

        def _spy(page_path, zones, request, **kwargs: object):
            calls.append((page_path, zones))
            return original(page_path, zones, request, **kwargs)

        monkeypatch.setattr(shaping_module, "render_zone", _spy)
        next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert len(calls) == 1
        _page_path, zones = calls[0]
        assert zones == ("wizard-zone",)


@pytest.mark.django_db()
class TestWizardAdvanceCsrfMeta:
    """A token rotation on a step advance stamps the CSRF meta on the morph.

    The success funnel and the invalid shape already prove the uniform
    stamp. The advance reads the rotation flag before the next step's zone
    re-render mints a token, so a login mid-wizard refreshes the document
    tokens on the same envelope that swaps the step.
    """

    def test_rotation_on_advance_stamps_csrf(
        self, next_client: NextClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The submit request rotated its token, modelled by forcing the flag
        # the shaper reads before any step re-render mints a fresh one.
        monkeypatch.setattr(shaping_module, "_csrf_rotated", lambda _request: True)
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        envelope = envelope_of(response).data
        assert "csrf" in envelope
        assert envelope["csrf"]["token"]

    def test_no_rotation_leaves_no_csrf_on_advance(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert "csrf" not in envelope_of(response).data


@pytest.mark.django_db()
class TestWizardInvalidStepZoneMorph:
    """An invalid wizard step morphs the bound form with errors into the zone.

    The submitted value rides the bound form into the zone body, so the
    error state renders rather than an empty unbound step.
    """

    def test_invalid_step_zone_carries_bound_submitted_value(
        self, next_client: NextClient
    ) -> None:
        next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        too_long = "x" * 101
        response = next_client.post_action(
            "step_wizard",
            {"scope": too_long},
            origin="/wizard/scope/",
            partial=True,
            zones="wizard-zone",
        )
        assert response["X-Next-Form"] == "invalid"
        html = envelope_of(response).html_for_zone("wizard-zone")
        assert too_long in html


@pytest.mark.django_db()
class TestWizardAdvanceStorageBudget:
    """One round trip to wizard storage per advanced step.

    A failing assertion means the partial advance grew a storage
    round-trip. The mid-step submit pays one save for the draft and one
    load for the next step's prefilled form, nothing more.
    """

    def test_partial_advance_pays_one_save_and_one_load(
        self, next_client: NextClient, counting_wizard_backend: CountingWizardBackend
    ) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert response.status_code == 200
        assert counting_wizard_backend.saves == 1
        assert counting_wizard_backend.loads == 1
        assert counting_wizard_backend.clears == 0
