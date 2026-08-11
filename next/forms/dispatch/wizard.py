"""Wizard step dispatch and the validate-only short circuit."""

import time
from typing import TYPE_CHECKING

from django.http import HttpResponse, HttpResponseBadRequest

from next.deps import resolver
from next.ports import partial_shaper_slot

from .permissions import _enforce_object_permissions, _enforce_view_permissions
from .responses import (
    _HTTP_ERROR_FLOOR,
    ActionOutcome,
    ActionOutcomeKind,
    _flash_success_before_rerender,
    _send_success_message,
)


if TYPE_CHECKING:
    from django import forms as django_forms
    from django.http import HttpRequest

    from next.forms.backends import FormActionBackend
    from next.forms.wizard import FormWizard

    from . import _DispatchState


def _maybe_validate_only(
    backend: "FormActionBackend",
    request: "HttpRequest",
    form: "django_forms.Form",
    action_name: str,
    state: "_DispatchState",
) -> "HttpResponse | None":
    """Return a validate-only response when the request asks for one.

    The branch only fires once both authorization layers have passed and
    the form is already bound, so a guarded action's validator is never an
    anonymous oracle. The handler never runs, success signals never fire,
    and wizard storage stays untouched. A request without validate fields
    falls through to the normal submit path.
    """
    shaper = partial_shaper_slot.get()
    if not shaper.intent(request).validate_fields:
        return None
    return shaper.shape_validate(
        backend, request, form, action_name=action_name, uid=state.uid or ""
    )


def _bind_wizard_step(
    backend: "FormActionBackend",
    request: "HttpRequest",
    action_name: str,
    wizard_class: "type[FormWizard]",
    state: "_DispatchState",
) -> "HttpResponse | tuple[FormWizard, str, django_forms.Form]":
    """Authorize the step POST and bind its form, or return an early response.

    Both permission layers and the validate-only short circuit run here
    before any storage write, so a guarded validator never runs for an
    unauthorized caller.
    """
    if state.origin_match is None:
        return HttpResponseBadRequest("Missing or invalid _next_form_origin")
    denial = _enforce_view_permissions(wizard_class, request, action_name, state)
    if denial is not None:
        return denial
    wizard = wizard_class(
        request=request,
        url_kwargs=state.url_kwargs,
        base_path=state.origin_match.origin,
    )
    step_name = wizard.current_step()
    form_class = wizard.step_form_class(step_name)
    if form_class is None:
        return HttpResponseBadRequest("Unknown wizard step")
    form_kwargs = wizard.get_form_kwargs(step_name)
    files = request.FILES if hasattr(request, "FILES") else None
    form = form_class(request.POST, files, **form_kwargs)
    denial = _enforce_object_permissions(form, request, action_name, state)
    if denial is not None:
        return denial
    validated = _maybe_validate_only(backend, request, form, action_name, state)
    if validated is not None:
        return validated
    return wizard, step_name, form


def _dispatch_wizard(
    backend: "FormActionBackend",
    request: "HttpRequest",
    action_name: str,
    wizard_class: "type[FormWizard]",
    state: "_DispatchState",
) -> HttpResponse:
    """Validate the current wizard step, then route forward or finalise."""
    bound = _bind_wizard_step(backend, request, action_name, wizard_class, state)
    if isinstance(bound, HttpResponse):
        return bound
    wizard, step_name, form = bound
    if not form.is_valid():
        state.emit_form_validation_failed(request, action_name, form)
        return backend.shape_response(
            request,
            ActionOutcome(
                kind=ActionOutcomeKind.INVALID,
                action_name=action_name,
                uid=state.uid,
                form=form,
                url_kwargs=state.url_kwargs,
                wizard=wizard,
                page_path=state.page_path,
                origin=state.origin,
            ),
        )

    cleaned = dict(form.cleaned_data)
    wizard.save_step(step_name, cleaned)
    state.emit_wizard_step_submitted(request, wizard_class, step_name, cleaned)

    next_step = wizard.next_step(step_name)
    if next_step is None:
        # A direct POST to the last step must not finalise while an
        # earlier step has no stored data, so reroute to the first gap.
        next_step = wizard.first_incomplete_step()
    if next_step is None:
        merged = wizard.get_all_cleaned_data()
        resolved = resolver.resolve_dependencies(
            wizard.done,
            request=request,
            cleaned_data=merged,
            _cache=state.dep_cache,
            _stack=state.dep_stack,
            **state.url_kwargs,
        )
        start = time.perf_counter()
        raw = wizard.done(**resolved)
        duration_ms = (time.perf_counter() - start) * 1000
        flashed = _flash_success_before_rerender(
            request, wizard, merged, raw, state.page_path
        )
        response = backend.shape_response(
            request,
            ActionOutcome(
                kind=ActionOutcomeKind.RESULT,
                action_name=action_name,
                uid=state.uid,
                raw=raw,
                form=form,
                wizard=wizard,
            ),
        )
        if response.status_code < _HTTP_ERROR_FLOOR:
            if not flashed:
                _send_success_message(request, wizard, merged)
            wizard.clear_storage()
            state.emit_wizard_completed(request, wizard_class, merged)
    else:
        response = backend.shape_response(
            request,
            ActionOutcome(
                kind=ActionOutcomeKind.WIZARD_ADVANCE,
                action_name=action_name,
                uid=state.uid,
                redirect_to=wizard.goto(next_step),
                wizard=wizard,
            ),
        )
        duration_ms = 0.0

    state.emit_action_dispatched(request, action_name, form, duration_ms, response)
    return response
