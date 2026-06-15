import pytest
from django.http import HttpResponseRedirect
from django.test import RequestFactory

from next.forms.dispatch import ActionOutcome, ActionOutcomeKind
from next.forms.manager import form_action_manager
from next.partial import Patches, shape_partial, shaping as shaping_module
from next.partial.headers import REQUEST_FLAG


_PARTIAL_META = {f"HTTP_{REQUEST_FLAG.upper().replace('-', '_')}": "1"}


def _partial_request():
    """Return a partial POST that names no resolvable origin page."""
    return RequestFactory().post("/_next/form/x/", data={}, **_PARTIAL_META)


class TestWizardAdvanceDelegationStub:
    """A WIZARD_ADVANCE outcome delegates to the full-path redirect for now."""

    def test_advance_returns_the_step_redirect(self) -> None:
        backend = form_action_manager.default_backend
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.WIZARD_ADVANCE,
            action_name="step_form",
            uid="abcd",
            redirect_to="/request/scope/",
        )
        response = shape_partial(backend, _partial_request(), outcome)
        assert isinstance(response, HttpResponseRedirect)
        assert response["Location"] == "/request/scope/"


class TestFormZoneWithoutAResolvedPage:
    """`_form_zone` returns None when the origin resolves to no page."""

    def test_none_page_path_yields_no_zone(self) -> None:
        zone = shaping_module._form_zone(_partial_request(), None)
        assert zone is None


class TestFormOverridesWithoutAForm:
    """`_form_overrides` yields an empty mapping when the outcome has no form."""

    def test_no_form_yields_no_overrides(self) -> None:
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.INVALID,
            action_name="step_form",
            form=None,
        )
        assert shaping_module._form_overrides(outcome) == {}


class TestBuilderRenderHelpersRequireAResolvablePage:
    """A render helper raises when the request origin resolves to no page."""

    def test_zone_morph_on_an_unresolved_origin_raises(self) -> None:
        builder = Patches(_partial_request())
        with pytest.raises(RuntimeError, match="does not resolve to a page"):
            builder.morph(zone="alpha")
