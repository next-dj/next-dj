"""Shaping of form action outcomes into patch envelopes for partial requests."""

import types
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from django.contrib.messages import get_messages
from django.core.exceptions import NON_FIELD_ERRORS
from django.forms import BaseForm, BaseFormSet, FileField
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from next.forms.dispatch import ActionOutcome, ActionOutcomeKind, FormActionDispatch
from next.forms.origin import resolve_origin, resolve_url_to_match
from next.forms.uid import FORM_ORIGIN_OVERRIDE_KEY
from next.pages import page
from next.static.scripts import csrf_payload

from .headers import RESPONSE_ACTION, RESPONSE_FORM, partial_intent
from .manager import partial_backend_manager
from .patches import FormMeta, Patches, PatchResponse
from .registry import zones_of
from .render import render_zone
from .signals import field_validated


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest

    from next.forms.backends import FormActionBackend
    from next.forms.wizard import FormWizard

    from .headers import PartialIntent


_MESSAGE_VARIANTS: dict[str, str] = {
    "debug": "info",
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "error",
}

# Django private META key its CsrfViewMiddleware sets when a rotated token
# needs writing back. Stable across 4.2 to 6.0. A canary test fails loudly if a
# future Django renames it, so rotated tokens never silently stop being stamped.
_CSRF_ROTATED_FLAG = "CSRF_COOKIE_NEEDS_UPDATE"
_PUSH_WIZARD_STEPS_OPTION = "PUSH_WIZARD_STEPS"


class _NonFormErrors(Protocol):
    """The writable non-form-errors attribute Django keeps no setter for."""

    _non_form_errors: object


@dataclass(frozen=True, slots=True)
class ActionRef:
    """The registry identity of the action a validate pass shapes."""

    action_name: str
    uid: str


def shape_partial(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
) -> HttpResponse:
    """Shape one action outcome as a patch envelope for a partial request.

    The CSRF rotation marker is read here, before any form or zone
    re-render mints a token and sets the marker as a side effect, so a
    login on the submit path stamps the fresh token onto whichever shape
    the outcome takes. Reading it after a re-render would flag every
    response as rotated.
    """
    rotated = _csrf_rotated(request)
    if outcome.kind == ActionOutcomeKind.INVALID:
        return _shape_invalid(backend, request, outcome, rotated=rotated)
    if outcome.kind == ActionOutcomeKind.WIZARD_ADVANCE:
        return _shape_advance(request, outcome, rotated=rotated)
    return _shape_result(backend, request, outcome, rotated=rotated)


def shape_validate(
    backend: "FormActionBackend",
    request: "HttpRequest",
    form: "BaseForm | BaseFormSet",
    intent: "PartialIntent",
    action: ActionRef,
) -> HttpResponse:
    """Shape a validate-only pass as a form morph envelope.

    The form is already bound and both authorization layers have passed,
    so running `is_valid()` here never leaks a guarded validator to an
    anonymous caller. The handler stays unrun, success signals stay
    silent, and wizard storage stays untouched. Errors are filtered to the
    fields the request named, never-submitted fields keep no premature
    required error, the cross-field non-field errors are always dropped,
    and file fields are excluded from the requested set. The response is
    always 200 with a form morph by uid plus the surviving errors in meta.
    """
    rotated = _csrf_rotated(request)
    form.is_valid()
    requested = _validate_targets(form, intent.validate_fields)
    _scrub_errors(form, requested)

    uid = action.uid
    page_path, url_kwargs = _origin_target(request)
    patches = Patches(request)
    zone = _form_zone(request, page_path)
    if zone is not None:
        overrides = _bound_form_overrides(form, action.action_name)
        patches.morph(zone=zone, overrides=overrides)
    else:
        html = backend.render_invalid_page(
            request,
            action.action_name,
            form,
            page_path,
            url_kwargs,
        )
        patches.morph({"form": uid}, html, extract=True)
    patches.set_form(_form_meta(uid, form))
    _stamp_csrf(request, patches, rotated=rotated)
    _emit_field_validated(request, action, requested, form)
    return _envelope_response(patches)


def drain_messages(request: "HttpRequest", patches: Patches) -> Patches:
    """Drain pending contrib.messages into toast patches.

    Iterating `get_messages` marks the messages read, so a later full
    navigation does not replay them. The success message of an action
    becomes a toast for free.
    """
    for message in get_messages(request):
        variant = _MESSAGE_VARIANTS.get(message.level_tag, "info")
        patches.toast(str(message), variant=variant)
    return patches


def _shape_invalid(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
    *,
    rotated: bool,
) -> HttpResponse:
    """Shape an invalid submission as a patch addressing only the failed form.

    The target chain is the zone named by the partial intent, then the
    form by uid. A named zone re-renders only that zone with the bound
    form in overrides. Without a zone the whole origin page re-renders
    and the patch carries `extract: true`, so the client trims the failed
    form out of the document by its uid. Neighbouring forms and zones are
    addressed by no operation. The existing invalid-form headers stay.
    """
    patches = Patches(request)
    form = outcome.form
    uid = outcome.uid or ""
    zone = _form_zone(request, outcome.page_path)
    if zone is not None:
        patches.morph(zone=zone, overrides=_form_overrides(outcome))
    else:
        html = backend.render_invalid_page(
            request,
            outcome.action_name,
            form,
            outcome.page_path,
            outcome.url_kwargs,
        )
        patches.morph({"form": uid}, html, extract=True)
    if form is not None:
        patches.set_form(_form_meta(uid, form))
    response = _envelope_response(patches, request=request, rotated=rotated)
    response[RESPONSE_FORM] = "invalid"
    if outcome.uid:
        response[RESPONSE_ACTION] = outcome.uid
    return response


def _shape_advance(
    request: "HttpRequest",
    outcome: ActionOutcome,
    *,
    rotated: bool,
) -> HttpResponse:
    """Shape a wizard step advance as a master-zone morph, never a redirect.

    The advance carries a live wizard and the URL of the next step. The
    URL resolves through the URLconf to the next step's page identity, a
    second wizard binds to that page and yields the unbound next-step
    form, and the master zone of the next step renders into a morph. The
    next step page view never runs, so wizard authorization must live in
    the action guard. A history `url.push` rides along only when the
    wizard opts into pushing steps, off by default.
    """
    wizard = outcome.wizard
    redirect_to = outcome.redirect_to
    if redirect_to is None:
        return HttpResponse(status=204)
    if wizard is None:
        return HttpResponseRedirect(redirect_to)
    target = _resolve_step_target(request, redirect_to)
    if target is None:
        return HttpResponseRedirect(redirect_to)
    page_path, url_kwargs = target
    next_wizard = type(wizard)(
        request=request,
        url_kwargs=url_kwargs,
        base_path=redirect_to,
    )
    form = next_wizard.current_form()
    patches = Patches(request)
    zone = _form_zone(request, page_path)
    if zone is not None:
        overrides = _wizard_overrides(form, next_wizard, outcome.action_name)
        # Override the form origin in the render context so the next step's
        # hidden _next_form_origin field carries the next step URL, not the
        # current step URL from request.POST. Without this, blur-validate
        # probes on the new step resolve the origin back to the previous step
        # page and morph the wrong step into the zone.
        overrides[FORM_ORIGIN_OVERRIDE_KEY] = redirect_to
        result = render_zone(
            page_path, (zone,), request, url_kwargs=url_kwargs, overrides=overrides
        )
        patches.morph({"zone": zone}, result.html[zone])
    if _should_push_steps(wizard):
        patches.push_url(redirect_to)
    return _envelope_response(patches, request=request, rotated=rotated)


def _shape_result(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
    *,
    rotated: bool,
) -> HttpResponse:
    """Shape a successful outcome, packing redirects and the success funnel.

    A handler that returned a `PatchResponse` already authored the
    envelope and passes through. An `HttpResponseRedirect` is packed into
    a `visit`. A `None` result is the success funnel: instead of a full
    origin re-render the failed-form zone or the form is morphed in place
    and pending messages drain to toasts.
    """
    raw = outcome.raw
    if isinstance(raw, PatchResponse):
        return raw
    if isinstance(raw, HttpResponseRedirect):
        return _redirect_as_visit(request, raw, rotated=rotated)
    if raw is None:
        return _success_funnel(backend, request, outcome, rotated=rotated)
    return FormActionDispatch.shape_response(backend, request, outcome)


def _redirect_as_visit(
    request: "HttpRequest",
    redirect: HttpResponseRedirect,
    *,
    rotated: bool,
) -> HttpResponse:
    """Pack a handler redirect into a `visit`, full-navigating external hosts.

    A same-site URL travels as an internal visit the validator approves.
    A server-authored external URL such as an OAuth or payment gateway
    travels with a full-navigation marker so it is not rejected by the
    same-host validator. The external branch trusts the handler's redirect
    target, so a handler must never build it from user input or the page
    becomes an open redirect.
    """
    href = redirect["Location"]
    internal = url_has_allowed_host_and_scheme(
        href,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    patches = Patches(request)
    patches.redirect(href, external=not internal)
    return _envelope_response(patches, request=request, rotated=rotated)


def _success_funnel(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
    *,
    rotated: bool,
) -> HttpResponse:
    """Morph the form in place on a None result and drain messages to toasts."""
    page_path, url_kwargs = _origin_target(request)
    patches = Patches(request)
    uid = outcome.uid or ""
    zone = _form_zone(request, page_path)
    if zone is not None:
        patches.morph(zone=zone)
    else:
        html = backend.render_invalid_page(
            request,
            outcome.action_name,
            None,
            page_path,
            url_kwargs,
        )
        patches.morph({"form": uid}, html, extract=True)
    drain_messages(request, patches)
    return _envelope_response(patches, request=request, rotated=rotated)


def _origin_target(
    request: "HttpRequest",
) -> "tuple[Path | None, dict[str, object]]":
    """Resolve the request origin to its page path and URL kwargs.

    Both the validate pass and the success funnel re-render the origin
    page, so they share one resolution. A request that names no resolvable
    origin yields a None page path and empty kwargs.
    """
    match = resolve_origin(request)
    if match is None:
        return None, {}
    return match.page_path, dict(match.url_kwargs)


def _form_zone(request: "HttpRequest", page_path: "Path | None") -> str | None:
    """Return the zone the failed form lives in, or None for the form-by-uid path.

    The zone named by the partial intent wins when the origin page
    declares it, so a form submitted from inside a zone re-renders only
    that zone. A request that names no declared zone falls through to the
    extract-morph of the form by uid.
    """
    if page_path is None:
        return None
    zones = partial_intent(request).zones
    if not zones:
        return None
    declared = _declared_zones(page_path)
    return next((name for name in zones if name in declared), None)


def _declared_zones(page_path: "Path") -> frozenset[str]:
    """Return the zone names the origin page declares."""
    template = page.composed_template_for(page_path)
    return frozenset(zones_of(template))


def _form_overrides(outcome: ActionOutcome) -> dict[str, object]:
    """Return the context overrides that bind the failed form into the zone."""
    form = outcome.form
    if form is None:
        return {}
    wizard = outcome.wizard
    if wizard is not None:
        namespace = types.SimpleNamespace(form=form, wizard=wizard)
        return {"form": form, "wizard": wizard, outcome.action_name: namespace}
    return {"form": form, outcome.action_name: form}


def _bound_form_overrides(
    form: "BaseForm | BaseFormSet",
    action_name: str,
) -> dict[str, object]:
    """Return the overrides binding an already-bound form into the zone."""
    return {"form": form, action_name: form}


def _wizard_overrides(
    form: object,
    wizard: "FormWizard",
    action_name: str,
) -> dict[str, object]:
    """Return the overrides binding the next step's unbound form into the zone."""
    namespace = types.SimpleNamespace(form=form, wizard=wizard)
    return {"form": form, "wizard": wizard, action_name: namespace}


def _resolve_step_target(
    request: "HttpRequest",
    href: str,
) -> "tuple[Path, dict[str, object]] | None":
    """Resolve the next step URL to its page identity and URL kwargs.

    The URL travels through the same URLconf the origin uses, so the next
    step's page path and captured kwargs come from one resolution without
    running the step page view. The captured kwargs stay unfiltered so the
    next step renders with every URL parameter it declares.
    """
    match = resolve_url_to_match(href, request, filter_reserved=False)
    if match is None or match.page_path is None:
        return None
    return match.page_path, dict(match.url_kwargs)


def _should_push_steps(wizard: "FormWizard") -> bool:
    """Return True when the wizard opts into pushing its steps to history."""
    options = partial_backend_manager.get().options
    default = bool(options.get(_PUSH_WIZARD_STEPS_OPTION, False))
    meta = getattr(wizard, "Meta", None)
    return bool(getattr(meta, "push_steps", default))


def _validate_targets(
    form: "BaseForm | BaseFormSet",
    validate_fields: tuple[str, ...],
) -> frozenset[str]:
    """Return the requested field names with file fields removed.

    A multipart file is never re-uploaded on a blur, so even if a client
    names a file field the server drops it from the validate target set.
    """
    files = _file_field_names(form)
    return frozenset(name for name in validate_fields if name not in files)


def _file_field_names(form: "BaseForm | BaseFormSet") -> frozenset[str]:
    """Return the file field names of a form or the prefixed names of a formset."""
    if isinstance(form, BaseFormSet):
        names: set[str] = set()
        for member in form.forms:
            names.update(
                f"{member.prefix}-{name}" for name in _form_file_fields(member)
            )
        return frozenset(names)
    return _form_file_fields(form)


def _form_file_fields(form: "BaseForm") -> frozenset[str]:
    """Return the names of the file fields declared on one bound form."""
    return frozenset(
        name for name, field in form.fields.items() if isinstance(field, FileField)
    )


def _scrub_errors(
    form: "BaseForm | BaseFormSet",
    requested: frozenset[str],
) -> None:
    """Drop every error the validate request did not ask to surface.

    Only errors of the requested fields survive. The cross-field non-field
    errors are always cleared because a `clean()` belongs to the submit,
    not to a per-field blur. A formset scrubs each member by the member's
    prefixed field names and clears its non-form errors too.
    """
    if isinstance(form, BaseFormSet):
        for member in form.forms:
            _scrub_member_errors(member, requested)
        # The non-form errors live on the protected attribute the formset
        # populates in full_clean, reset it so a cross-form clean never
        # surfaces on a per-field blur. Django keeps no public setter for it.
        cast("_NonFormErrors", form)._non_form_errors = form.error_class()
        return
    _scrub_form_errors(form, requested)


def _scrub_member_errors(form: "BaseForm", requested: frozenset[str]) -> None:
    """Scrub a formset member by its prefixed field names."""
    survivors = {
        name
        for name in form.errors
        if name != NON_FIELD_ERRORS and f"{form.prefix}-{name}" in requested
    }
    _keep_only(form, survivors)


def _scrub_form_errors(form: "BaseForm", requested: frozenset[str]) -> None:
    """Scrub a plain form by its bare field names."""
    survivors = {
        name for name in form.errors if name != NON_FIELD_ERRORS and name in requested
    }
    _keep_only(form, survivors)


def _keep_only(form: "BaseForm", survivors: set[str]) -> None:
    """Drop every error key of the form that is not in the survivor set."""
    for name in list(form.errors):
        if name not in survivors:
            del form.errors[name]


def _csrf_rotated(request: "HttpRequest") -> bool:
    """Return True when the request rotated its CSRF token.

    Django flags a rotated token on `request.META`, so the marker is read
    before any form re-render mints a token. A request whose META is not a
    real mapping cannot rotate and reads as not rotated.
    """
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return False
    return bool(meta.get(_CSRF_ROTATED_FLAG))


def _stamp_csrf(
    request: "HttpRequest",
    patches: Patches,
    *,
    rotated: bool,
) -> None:
    """Attach the rotated CSRF payload when the request rotated its token."""
    if rotated:
        patches.set_csrf(csrf_payload(request))


def _emit_field_validated(
    request: "HttpRequest",
    action: ActionRef,
    requested: frozenset[str],
    form: "BaseForm | BaseFormSet",
) -> None:
    """Announce a validated pass when the signal has receivers, always behind guard."""
    if not field_validated.receivers:
        return
    field_validated.send(
        sender=type(partial_backend_manager.get()),
        action_name=action.action_name,
        uid=action.uid,
        request=request,
        field_names=tuple(sorted(requested)),
        error_count=_error_count(form),
    )


def _error_count(form: "BaseForm | BaseFormSet") -> int:
    """Return the number of surviving field errors after scrubbing."""
    if isinstance(form, BaseFormSet):
        return sum(len(member.errors) for member in form.forms)
    return sum(len(errors) for errors in form.errors.values())


def _envelope_response(
    patches: Patches,
    *,
    request: "HttpRequest | None" = None,
    rotated: bool = False,
) -> PatchResponse:
    """Serialise the builder's envelope into a partial response.

    When a request rotated its CSRF token the fresh payload is stamped
    here so every shaped outcome carries it, not only the validate path.
    The `rotated` flag is read before any re-render by the caller, so the
    re-render's own `get_token` never registers as a fresh rotation.
    """
    if request is not None and rotated:
        _stamp_csrf(request, patches, rotated=rotated)
    backend_obj = partial_backend_manager.get()
    body = backend_obj.serialize_envelope(patches.envelope())
    return PatchResponse(
        body, content_type=backend_obj.content_type, version=patches.version
    )


def _form_meta(uid: str, form: "BaseForm | BaseFormSet") -> FormMeta:
    """Build the machine-readable form meta from a bound form's errors."""
    errors = _meta_errors(form)
    valid = not errors
    return FormMeta(uid=uid, valid=valid, errors=errors)


def _meta_errors(form: "BaseForm | BaseFormSet") -> dict[str, list[str]]:
    """Return the wire-form errors of a form or the prefixed errors of a formset."""
    if isinstance(form, BaseFormSet):
        merged: dict[str, list[str]] = {}
        for member in form.forms:
            for name, messages in member.errors.items():
                merged[f"{member.prefix}-{name}"] = [str(m) for m in messages]
        return merged
    return {
        name: [str(message) for message in messages]
        for name, messages in form.errors.items()
    }


__all__ = ["ActionRef", "drain_messages", "shape_partial", "shape_validate"]
