"""Shaping of a validate-only pass into a form morph envelope."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.partial.manager import partial_backend_manager
from next.partial.patches import Patches
from next.partial.signals import field_validated

from .csrf import _csrf_rotated, _stamp_csrf
from .responses import _envelope_response
from .scrub import _error_count, _form_meta, _scrub_errors, _validate_targets
from .targets import _form_zone, _origin_target, _zone_overrides


if TYPE_CHECKING:
    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse

    from next.forms.backends import FormActionBackend
    from next.forms.wizard import FormWizard
    from next.partial.headers import PartialIntent


@dataclass(frozen=True, slots=True)
class ActionRef:
    """The registry identity of the action a validate pass shapes."""

    action_name: str
    uid: str


def shape_validate(
    backend: "FormActionBackend",
    request: "HttpRequest",
    form: "BaseForm | BaseFormSet",
    intent: "PartialIntent",
    action: ActionRef,
    *,
    wizard: "FormWizard | None" = None,
) -> "HttpResponse":
    """Shape a validate-only pass as a form morph envelope.

    The origin page and both action layers have already authorized this request, so
    `is_valid()` never leaks a guarded validator to an anonymous caller.
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
        overrides = _zone_overrides(form, wizard, action.action_name)
        patches.morph(zone=zone, overrides=overrides)
    else:
        html = backend.render_invalid_page(
            request, action.action_name, form, page_path, url_kwargs
        )
        patches.morph_form(uid, html)
    patches.set_form(_form_meta(uid, form))
    _stamp_csrf(request, patches, rotated=rotated)
    _emit_field_validated(request, action, requested, form)
    return _envelope_response(patches)


def _emit_field_validated(
    request: "HttpRequest",
    action: ActionRef,
    requested: frozenset[str],
    form: "BaseForm | BaseFormSet",
) -> None:
    """Announce a validated pass, counting the errors only for a listener."""
    sender = type(partial_backend_manager.get())
    if not field_validated.has_listeners(sender):
        return
    field_validated.send(
        sender=sender,
        action_name=action.action_name,
        uid=action.uid,
        request=request,
        field_names=tuple(sorted(requested)),
        error_count=_error_count(form),
    )
