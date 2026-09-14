"""Resolution of the origin page, the zone, and the overrides a re-render needs."""

import types
from typing import TYPE_CHECKING

from next.forms.origin import resolve_origin, resolve_url_to_match
from next.pages import page
from next.partial.headers import partial_intent
from next.partial.manager import partial_backend_manager
from next.partial.registry import zones_of


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest

    from next.forms.wizard import FormWizard


_PUSH_WIZARD_STEPS_OPTION = "PUSH_WIZARD_STEPS"


def _origin_target(request: "HttpRequest") -> "tuple[Path | None, dict[str, object]]":
    """Resolve the request origin to its page path and URL kwargs.

    Shared by the validate pass and the success funnel, so both share one resolution.
    """
    match = resolve_origin(request)
    if match is None:
        return None, {}
    return match.page_path, dict(match.url_kwargs)


def _form_zone(request: "HttpRequest", page_path: "Path | None") -> str | None:
    """Return the zone the failed form lives in, or None for the form-by-uid path.

    The zone named by the partial intent wins when the origin page declares it, else
    falls through to the extract-morph of the form by uid.
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


def _zone_overrides(
    form: "BaseForm | BaseFormSet | None", wizard: "FormWizard | None", action_name: str
) -> dict[str, object]:
    """Return the overrides binding one form into a zone re-render.

    The `{% form %}` tag honours the action key only when it carries a `.form`
    attribute, so a bare form there makes the tag build an unbound replacement instead.
    """
    if form is None:
        return {}
    overrides: dict[str, object] = {
        "form": form,
        action_name: types.SimpleNamespace(form=form, wizard=wizard),
    }
    if wizard is not None:
        overrides["wizard"] = wizard
    return overrides


def _resolve_step_target(
    request: "HttpRequest", href: str
) -> "tuple[Path, dict[str, object]] | None":
    """Resolve the next step URL to its page identity and URL kwargs.

    Resolves through the same URLconf as the origin, without running the step page
    view. Kwargs stay unfiltered so the next step renders every parameter it declares.
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
