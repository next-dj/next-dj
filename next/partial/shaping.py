"""Shaping of form action outcomes into patch envelopes for partial requests."""

from typing import TYPE_CHECKING

from django.contrib.messages import get_messages
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from next.forms.dispatch import ActionOutcome, ActionOutcomeKind, FormActionDispatch
from next.forms.origin import _resolve_origin
from next.pages import page

from .backends import partial_backend_manager
from .headers import RESPONSE_ACTION, RESPONSE_FORM, partial_intent
from .patches import FormMeta, Patches, PatchResponse
from .registry import zones_of


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm
    from django.http import HttpRequest

    from next.forms.backends import FormActionBackend


_MESSAGE_VARIANTS: dict[str, str] = {
    "debug": "info",
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "error",
}


def shape_partial(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
) -> HttpResponse:
    """Shape one action outcome as a patch envelope for a partial request."""
    if outcome.kind == ActionOutcomeKind.INVALID:
        return _shape_invalid(backend, request, outcome)
    if outcome.kind == ActionOutcomeKind.WIZARD_ADVANCE:
        return FormActionDispatch.shape_response(backend, request, outcome)
    return _shape_result(backend, request, outcome)


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
    response = _envelope_response(patches)
    response[RESPONSE_FORM] = "invalid"
    if outcome.uid:
        response[RESPONSE_ACTION] = outcome.uid
    return response


def _shape_result(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
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
        return _redirect_as_visit(request, raw)
    if raw is None:
        return _success_funnel(backend, request, outcome)
    return FormActionDispatch.shape_response(backend, request, outcome)


def _redirect_as_visit(
    request: "HttpRequest",
    redirect: HttpResponseRedirect,
) -> HttpResponse:
    """Pack a handler redirect into a `visit`, full-navigating external hosts.

    A same-site URL travels as an internal visit the validator approves.
    A server-authored external URL such as an OAuth or payment gateway
    travels with a full-navigation marker so it is not rejected by the
    same-host validator.
    """
    href = redirect["Location"]
    internal = url_has_allowed_host_and_scheme(
        href,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    patches = Patches(request)
    patches.redirect(href, external=not internal)
    return _envelope_response(patches)


def _success_funnel(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
) -> HttpResponse:
    """Morph the form in place on a None result and drain messages to toasts."""
    match = _resolve_origin(request)
    page_path = match.page_path if match is not None else None
    url_kwargs = dict(match.url_kwargs) if match is not None else {}
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
    return _envelope_response(patches)


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
    return {"form": form, outcome.action_name: form}


def _envelope_response(patches: Patches) -> PatchResponse:
    """Serialise the builder's envelope into a partial response."""
    backend_obj = partial_backend_manager.get()
    body = backend_obj.serialize_envelope(patches.envelope())
    return PatchResponse(
        body, content_type=backend_obj.content_type, version=patches.version
    )


def _form_meta(uid: str, form: "BaseForm") -> FormMeta:
    """Build the machine-readable form meta from a bound form's errors."""
    errors = {
        name: [str(message) for message in messages]
        for name, messages in form.errors.items()
    }
    return FormMeta(uid=uid, valid=form.is_valid(), errors=errors)


__all__ = ["drain_messages", "shape_partial"]
