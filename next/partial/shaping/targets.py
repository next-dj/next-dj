"""Resolution of the origin page, the zone, and the overrides a re-render needs."""

import types
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied
from django.http import Http404

from next.forms.origin import resolve_origin, resolve_url_to_match
from next.partial.errors import ForeignPageNotAuthorizedError
from next.partial.headers import partial_intent
from next.partial.intercept import resolve_served_page
from next.partial.manager import partial_backend_manager
from next.partial.registry import standalone_zones


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest

    from next.forms.wizard import FormWizard


_PUSH_WIZARD_STEPS_OPTION = "PUSH_WIZARD_STEPS"


def _origin_target(request: "HttpRequest") -> "tuple[Path | None, dict[str, object]]":
    """Resolve the request origin to the page serving its zones and its URL kwargs.

    Shared by the validate pass and the success funnel, so both share one resolution.
    """
    match = resolve_origin(request)
    if match is None:
        return None, {}
    url_kwargs = dict(match.url_kwargs)
    return _served_target(request, match.page_path, url_kwargs), url_kwargs


def _served_target(
    request: "HttpRequest", page_path: "Path | None", url_kwargs: dict[str, object]
) -> "Path | None":
    """Return the page serving the zones the request names over the origin page.

    A refusing intercept is a 403 whatever its rule raises, as the action may have run.
    """
    if page_path is None:
        return None
    zones = partial_intent(request).zones
    try:
        return resolve_served_page(page_path, zones, request, url_kwargs)
    except (ForeignPageNotAuthorizedError, Http404) as exc:
        raise PermissionDenied from exc


def _form_zone(request: "HttpRequest", page_path: "Path | None") -> str | None:
    """Return the zone the failed form lives in, or None for the form-by-uid path.

    The zone named by the intent wins when the serving page declares it.
    """
    if page_path is None:
        return None
    zones = partial_intent(request).zones
    if not zones:
        return None
    declared = _declared_zones(page_path)
    return next((name for name in zones if name in declared), None)


def _declared_zones(page_path: "Path") -> frozenset[str]:
    """Return the zone names the page declares that render on their own."""
    return frozenset(standalone_zones(page_path))


def _zone_overrides(
    form: "BaseForm | BaseFormSet | None", wizard: "FormWizard | None", action_name: str
) -> dict[str, object]:
    """Return the overrides binding one form into a zone re-render.

    `{% form %}` honours the action key only with a `.form` attribute.
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

    Uses the origin's URLconf without running the step view, kwargs left unfiltered.
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
