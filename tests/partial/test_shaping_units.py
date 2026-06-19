
import pytest
from django import forms
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory
from django.urls import set_script_prefix

from next.forms.dispatch import ActionOutcome, ActionOutcomeKind
from next.forms.manager import form_action_manager
from next.partial import Patches, shape_partial, shaping as shaping_module
from next.partial.shaping import (
    ActionRef,
    _csrf_rotated,
    _error_count,
    _file_field_names,
    _form_meta,
    _meta_errors,
    _origin_target,
    _resolve_step_target,
    _scrub_errors,
    _should_push_steps,
    _stamp_csrf,
    _validate_targets,
)
from tests.support import partial_request


class _MemberForm(forms.Form):
    email = forms.EmailField()
    avatar = forms.FileField()


def _bound_formset():
    """Return a bound, invalid two-row formset with a file field per row."""
    factory = forms.formset_factory(_MemberForm, extra=2)
    data = {
        "form-TOTAL_FORMS": "2",
        "form-INITIAL_FORMS": "0",
        "form-0-email": "bad",
        "form-1-email": "",
    }
    formset = factory(data)
    formset.is_valid()
    return formset


class _PushWizard:
    class Meta:
        push_steps = True


class _PlainWizard:
    class Meta:
        url_param = "step"


class TestAdvanceWithoutAResolvableTarget:
    """The advance branch degrades to a redirect when it cannot shape a zone."""

    def test_missing_redirect_returns_no_content(self) -> None:
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.WIZARD_ADVANCE,
            action_name="step_wizard",
        )
        backend = form_action_manager.default_backend
        response = shape_partial(backend, partial_request(origin=None), outcome)
        assert response.status_code == 204

    def test_missing_wizard_returns_the_step_redirect(self) -> None:
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.WIZARD_ADVANCE,
            action_name="step_wizard",
            redirect_to="/wizard/scope/",
        )
        backend = form_action_manager.default_backend
        response = shape_partial(backend, partial_request(origin=None), outcome)
        assert isinstance(response, HttpResponseRedirect)
        assert response["Location"] == "/wizard/scope/"

    def test_unresolvable_target_returns_the_step_redirect(self) -> None:
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.WIZARD_ADVANCE,
            action_name="step_wizard",
            redirect_to="/no/such/route/",
            wizard=_PlainWizard(),
        )
        backend = form_action_manager.default_backend
        response = shape_partial(backend, partial_request(origin=None), outcome)
        assert isinstance(response, HttpResponseRedirect)
        assert response["Location"] == "/no/such/route/"


class TestPushStepsGate:
    """`Meta.push_steps` and the backend default drive the history push gate."""

    def test_meta_push_steps_enables_the_push(self) -> None:
        assert _should_push_steps(_PushWizard()) is True

    def test_default_off_without_meta_opt_in(self) -> None:
        assert _should_push_steps(_PlainWizard()) is False

    def test_backend_default_enables_the_push(self) -> None:
        backend = shaping_module.partial_backend_manager.get()
        original = backend._options
        backend._options = {"PUSH_WIZARD_STEPS": True}
        try:
            assert _should_push_steps(_PlainWizard()) is True
        finally:
            backend._options = original


class TestFormsetScrubbing:
    """Formset validate scrubbing keys off prefixed member field names."""

    def test_file_field_names_are_prefixed(self) -> None:
        formset = _bound_formset()
        assert _file_field_names(formset) == frozenset(
            {"form-0-avatar", "form-1-avatar"}
        )

    def test_validate_targets_drop_file_fields(self) -> None:
        formset = _bound_formset()
        targets = _validate_targets(formset, ("form-0-email", "form-0-avatar"))
        assert targets == frozenset({"form-0-email"})

    def test_scrub_keeps_only_requested_member_field(self) -> None:
        formset = _bound_formset()
        _scrub_errors(formset, frozenset({"form-0-email"}))
        assert set(formset.forms[0].errors) == {"email"}
        assert dict(formset.forms[1].errors) == {}

    def test_meta_errors_are_prefixed(self) -> None:
        formset = _bound_formset()
        _scrub_errors(formset, frozenset({"form-0-email"}))
        errors = _meta_errors(formset)
        assert list(errors) == ["form-0-email"]

    def test_error_count_sums_member_errors(self) -> None:
        formset = _bound_formset()
        _scrub_errors(formset, frozenset({"form-0-email"}))
        assert _error_count(formset) == 1


class TestPlainFormScrubbing:
    """A plain form scrub drops non-field and unrequested errors."""

    def test_non_field_error_is_always_dropped(self) -> None:
        class _Cross(forms.Form):
            name = forms.CharField()

            def clean(self) -> dict:
                super().clean()
                msg = "no cross-field on blur"
                raise forms.ValidationError(msg)

        form = _Cross({"name": "ok"})
        form.is_valid()
        _scrub_errors(form, frozenset({"name"}))
        assert "__all__" not in form.errors

    def test_form_meta_marks_valid_when_no_errors_survive(self) -> None:
        class _One(forms.Form):
            name = forms.CharField()

        form = _One({"name": "ok"})
        form.is_valid()
        meta = _form_meta("uid", form)
        assert meta.valid is True
        assert meta.errors == {}


class TestCsrfRotation:
    """The CSRF meta rides only on a rotated token."""

    def test_non_dict_meta_reads_as_not_rotated(self) -> None:
        request = RequestFactory().post("/")
        request.META = None  # type: ignore[assignment]
        assert _csrf_rotated(request) is False

    def test_rotated_request_stamps_the_csrf_payload(self) -> None:
        request = RequestFactory().post("/")
        patches = Patches("1")
        _stamp_csrf(request, patches, rotated=True)
        envelope = patches.envelope().as_dict()
        assert "csrf" in envelope
        assert "token" in envelope["csrf"]

    def test_unrotated_request_leaves_no_csrf_meta(self) -> None:
        request = RequestFactory().post("/")
        patches = Patches("1")
        _stamp_csrf(request, patches, rotated=False)
        assert "csrf" not in patches.envelope().as_dict()


class TestResultRichResponseFallThrough:
    """A rich non-redirect result still falls through to the full path."""

    def test_plain_response_is_not_an_envelope(self) -> None:
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.RESULT,
            action_name="x",
            raw=HttpResponse("<p>plain</p>"),
        )
        backend = form_action_manager.default_backend
        response = shape_partial(backend, partial_request(origin=None), outcome)
        assert response.content == b"<p>plain</p>"


class TestFormZoneWithoutAResolvedPage:
    """`_form_zone` returns None when the origin resolves to no page."""

    def test_none_page_path_yields_no_zone(self) -> None:
        zone = shaping_module._form_zone(partial_request(origin=None), None)
        assert zone is None


class TestOriginTarget:
    """`_origin_target` resolves the request origin to a page path and kwargs."""

    def test_unresolvable_origin_yields_none_and_empty_kwargs(self) -> None:
        page_path, url_kwargs = _origin_target(partial_request(origin=None))
        assert page_path is None
        assert url_kwargs == {}

    def test_resolvable_origin_yields_its_page_and_kwargs(self) -> None:
        request = partial_request(origin="/items/42/")
        page_path, url_kwargs = _origin_target(request)
        assert page_path is not None
        assert url_kwargs == {"id": 42}


class TestFormOverridesWithoutAForm:
    """`_form_overrides` yields an empty mapping when the outcome has no form."""

    def test_no_form_yields_no_overrides(self) -> None:
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.INVALID,
            action_name="step_form",
            form=None,
        )
        assert shaping_module._form_overrides(outcome) == {}


class TestResolveStepTarget:
    """`_resolve_step_target` resolves a step URL to its page identity."""

    def test_unrouted_view_yields_no_target(self) -> None:
        request = RequestFactory().post("/")
        assert _resolve_step_target(request, "/_next/form/deadbeef/") is None

    def test_script_prefix_is_stripped_before_resolution(self) -> None:
        set_script_prefix("/app/")
        request = RequestFactory().post("/")
        try:
            target = _resolve_step_target(request, "/app/wizard/identity/")
            assert target is not None
            _page_path, url_kwargs = target
            assert url_kwargs == {"step": "identity"}
        finally:
            set_script_prefix("/")


class TestActionRefIdentity:
    """`ActionRef` carries the action name and uid the validate pass shapes."""

    def test_fields_are_readable(self) -> None:
        ref = ActionRef(action_name="x", uid="u")
        assert (ref.action_name, ref.uid) == ("x", "u")


class TestBuilderRenderHelpersRequireAResolvablePage:
    """A render helper raises when the request origin resolves to no page."""

    def test_zone_morph_on_an_unresolved_origin_raises(self) -> None:
        builder = Patches(partial_request(origin=None))
        with pytest.raises(RuntimeError, match="does not resolve to a page"):
            builder.morph(zone="alpha")
