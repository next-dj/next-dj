from pathlib import Path

import pytest

from next.pages.loaders import _load_python_module
from next.partial import (
    ForeignPageNotAuthorizedError,
    Patches,
    patches as patches_module,
)
from next.testing import NextClient, envelope_of
from tests.support import partial_request


_SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"
_ZONED_PAGE = _SITE_PAGES / "zoned" / "page.py"
_REDIRECTING_PAGE = _SITE_PAGES / "redirecting" / "page.py"
_GUARD_WIZARD_PAGE = _SITE_PAGES / "guardwizard" / "[step]" / "page.py"


class TestOriginZoneAuthorization:
    """A posted origin whose page denies the request never morphs its zone."""

    def test_denying_origin_raises_not_authorized(self) -> None:
        patches = Patches(partial_request(origin="/redirecting/"))
        with pytest.raises(ForeignPageNotAuthorizedError) as exc:
            patches.morph(zone="alpha")
        assert exc.value.page_path == _REDIRECTING_PAGE
        assert exc.value.status_code == 302

    def test_no_op_is_recorded_on_the_denial(self) -> None:
        patches = Patches(partial_request(origin="/redirecting/"))
        with pytest.raises(ForeignPageNotAuthorizedError):
            patches.morph(zone="alpha")
        assert patches.envelope().ops == ()

    def test_unresolvable_origin_still_raises_runtime_error(self) -> None:
        patches = Patches(partial_request(origin=None))
        with pytest.raises(RuntimeError, match="does not resolve to a page"):
            patches.morph(zone="alpha")


class TestAuthorizingOriginStillMorphs:
    """An origin page that would serve the request morphs exactly as before."""

    def test_zone_morph_carries_the_zone_body(self) -> None:
        envelope = Patches(partial_request()).morph(zone="alpha").envelope()
        op = envelope.ops[0].as_dict()
        assert op["target"] == {"zone": "alpha"}
        assert op["html"] == '<div data-next-zone="alpha"><p>alpha hi</p></div>'

    def test_origin_authorization_runs_once_per_builder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Path] = []
        original = patches_module.page_manager.authorization_outcome

        def _spy(page_path, request, visit_url, url_kwargs=None):
            calls.append(page_path)
            return original(page_path, request, visit_url, url_kwargs)

        monkeypatch.setattr(patches_module.page_manager, "authorization_outcome", _spy)
        Patches(partial_request()).morph(zone="alpha").morph(zone="beta")
        assert calls == [_ZONED_PAGE]


@pytest.fixture()
def guard_wizard_client() -> NextClient:
    """Register the guarded wizard and return a client that submits its steps."""
    _load_python_module(_GUARD_WIZARD_PAGE)
    return NextClient(enforce_csrf_checks=False)


def _advance_open_step(client: NextClient, **kwargs) -> object:
    return client.post_action(
        "sealed_wizard",
        {"name": "Ada"},
        origin="/guardwizard/open/",
        partial=True,
        **kwargs,
    )


@pytest.mark.django_db()
class TestWizardStepAuthorization:
    """A step page that refuses to serve itself is never morphed into the reply."""

    def test_advance_into_a_denying_step_falls_back_to_the_redirect(
        self, guard_wizard_client: NextClient
    ) -> None:
        response = _advance_open_step(guard_wizard_client, zones="sealed-zone")
        assert response.status_code == 302
        assert response["Location"] == "/guardwizard/sealed/"

    def test_no_step_zone_content_travels_on_the_denial(
        self, guard_wizard_client: NextClient
    ) -> None:
        response = _advance_open_step(guard_wizard_client, zones="sealed-zone")
        assert b"sealed-note" not in response.content

    def test_denial_without_a_named_zone_is_the_same_redirect(
        self, guard_wizard_client: NextClient
    ) -> None:
        response = _advance_open_step(guard_wizard_client)
        assert response.status_code == 302
        assert response["Location"] == "/guardwizard/sealed/"


@pytest.mark.django_db()
class TestAuthorizedWizardAdvanceIsUntouched:
    """A step page with no guard of its own still morphs on the advance."""

    def test_advance_morphs_the_next_step_zone(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
        )
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["wizard-zone"]
