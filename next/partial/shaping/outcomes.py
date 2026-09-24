"""Routing of one action outcome to the patch envelope that answers it."""

from typing import TYPE_CHECKING

from django.contrib.messages import get_messages
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.responses import ActionOutcome, ActionOutcomeKind
from next.forms.origin import filter_reserved_url_kwargs
from next.forms.uid import FORM_ORIGIN_OVERRIDE_KEY
from next.pages import page as page_manager
from next.partial import keys
from next.partial.headers import RESPONSE_ACTION, RESPONSE_FORM, set_partial_vary
from next.partial.patches import Patches, PatchResponse
from next.partial.render import render_zone

from .csrf import _csrf_rotated
from .responses import _envelope_response
from .scrub import _form_meta
from .targets import (
    _form_zone,
    _origin_target,
    _resolve_step_target,
    _should_push_steps,
    _zone_overrides,
)


if TYPE_CHECKING:
    from pathlib import Path

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
    backend: "FormActionBackend", request: "HttpRequest", outcome: ActionOutcome
) -> HttpResponse:
    """Shape one action outcome as a patch envelope for a partial request.

    The rotation marker is read before any re-render mints a token, so a login on
    submit stamps the fresh token, reading it after would flag every response instead.
    """
    rotated = _csrf_rotated(request)
    if outcome.kind == ActionOutcomeKind.INVALID:
        return _shape_invalid(backend, request, outcome, rotated=rotated)
    if outcome.kind == ActionOutcomeKind.WIZARD_ADVANCE:
        return _shape_advance(backend, request, outcome, rotated=rotated)
    return _shape_result(backend, request, outcome, rotated=rotated)


def drain_messages(request: "HttpRequest", patches: Patches) -> Patches:
    """Drain pending contrib.messages into toast patches.

    Iterating `get_messages` marks the messages read, so a later full navigation does
    not replay them. The success message of an action becomes a toast for free.
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

    A named zone re-renders just that zone with the bound form in overrides. Without
    one, the patch carries `extract: true` so the client trims the failed form by uid.
    """
    patches = Patches(request)
    form = outcome.form
    uid = outcome.uid or ""
    zone = _form_zone(request, outcome.page_path)
    if zone is not None:
        overrides = _zone_overrides(form, outcome.wizard, outcome.action_name)
        patches.morph(zone=zone, overrides=overrides)
    else:
        html = backend.render_invalid_page(
            request, outcome.action_name, form, outcome.page_path, outcome.url_kwargs
        )
        patches.morph_form(uid, html)
    if form is not None:
        patches.set_form(_form_meta(uid, form))
    response = _envelope_response(patches, request=request, rotated=rotated)
    response[RESPONSE_FORM] = "invalid"
    if outcome.uid:
        response[RESPONSE_ACTION] = outcome.uid
    return response


def _shape_advance(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
    *,
    rotated: bool,
) -> HttpResponse:
    """Shape a wizard step advance as a master-zone morph, never a redirect.

    The next step's page authorizes the request before its zone renders, and a denial
    falls back to the plain redirect a runtime-free client would have followed.
    """
    wizard = outcome.wizard
    redirect_to = outcome.redirect_to
    if redirect_to is None:
        response = HttpResponse(status=204)
        set_partial_vary(response)
        return response
    if wizard is None:
        return _step_redirect(redirect_to)
    target = _resolve_step_target(request, redirect_to)
    if target is None:
        return _step_redirect(redirect_to)
    page_path, url_kwargs = target
    if _step_denies(request, page_path, url_kwargs, redirect_to):
        return _step_redirect(redirect_to)
    next_wizard = type(wizard)(
        request=request, url_kwargs=url_kwargs, base_path=redirect_to
    )
    form = next_wizard.current_form()
    patches = Patches(request)
    overrides = _zone_overrides(form, next_wizard, outcome.action_name)
    # The hidden _next_form_origin must carry the next step URL, not the current
    # one from request.POST, or a blur probe morphs the previous step back in.
    overrides[FORM_ORIGIN_OVERRIDE_KEY] = redirect_to
    zone = _form_zone(request, page_path)
    if zone is not None:
        _advance_zone(patches, page_path, zone, request, url_kwargs, overrides)
    else:
        html = backend.render_invalid_page(
            request,
            outcome.action_name,
            form,
            page_path,
            url_kwargs,
            overrides=overrides,
        )
        patches.morph_form(outcome.uid or "", html)
    if _should_push_steps(wizard):
        patches.push_url(redirect_to)
    return _envelope_response(patches, request=request, rotated=rotated)


def _step_redirect(redirect_to: str) -> HttpResponse:
    """Return the plain step redirect a client without the runtime follows."""
    response = HttpResponseRedirect(redirect_to)
    set_partial_vary(response)
    return response


def _step_denies(
    request: "HttpRequest",
    page_path: "Path",
    url_kwargs: dict[str, object],
    step_url: str,
) -> bool:
    """Return True when the next step's page refuses to serve a visit of `step_url`.

    The step page keeps every captured parameter for its render, so the guard sees
    only the DI-safe subset, the same one the page's own view would be injected with.
    """
    denial, _dynamic = page_manager.authorization_outcome(
        page_path, request, step_url, filter_reserved_url_kwargs(url_kwargs)
    )
    return denial is not None


def _advance_zone(
    patches: Patches,
    page_path: "Path",
    zone: str,
    request: "HttpRequest",
    url_kwargs: dict[str, object],
    overrides: dict[str, object],
) -> None:
    """Render the next step's zone and morph it with its co-located assets.

    Renders directly rather than through `morph_zone`, since the next step lives on a
    foreign page, and forwards the manifest and js-context delta along with it.
    """
    result = render_zone(
        page_path, (zone,), request, url_kwargs=url_kwargs, overrides=overrides
    )
    patches.morph({keys.ZONE: zone}, result.html[zone])
    patches.absorb_zone_result(result)


def _shape_result(
    backend: "FormActionBackend",
    request: "HttpRequest",
    outcome: ActionOutcome,
    *,
    rotated: bool,
) -> HttpResponse:
    """Shape a successful outcome, packing redirects and the success funnel.

    A `None` result runs the success funnel,
    morphing in place instead of a full origin re-render.
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
    request: "HttpRequest", redirect: HttpResponseRedirect, *, rotated: bool
) -> HttpResponse:
    """Pack a handler redirect into a `visit`, full-navigating external hosts.

    An external URL gets a full-navigation marker past the same-host validator.
    Never build it from user input, or it opens a redirect.
    """
    href = redirect["Location"]
    internal = url_has_allowed_host_and_scheme(
        href, allowed_hosts={request.get_host()}, require_https=request.is_secure()
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
            request, outcome.action_name, None, page_path, url_kwargs
        )
        patches.morph_form(uid, html)
    drain_messages(request, patches)
    return _envelope_response(patches, request=request, rotated=rotated)
