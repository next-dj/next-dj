"""The layer a link to an intercepted page opens under `INTERCEPT = "links"`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.conf.router import InterceptMode, router_settings
from next.intercept import intercepts
from next.partial.headers import partial_intent
from next.partial.patches import Patches
from next.partial.registry import zones_served

from .clearing import stamp
from .envelopes import answered
from .intent import origin_path, target_href
from .shedding import refused_prefetch


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase


def _layer_zone(file_path: Path) -> str | None:
    """Return the first zone the first intercept of `file_path` serves, by file order.

    A zone GET of the page with that zone selects the same intercept.
    """
    for rule in intercepts.of(file_path):
        served = zones_served(rule.file, file_path)
        if served:
            return served[0]
    return None


def intercepted(request: HttpRequest, file_path: Path) -> HttpResponseBase | None:
    """Answer a link to an intercepted page with its layer under `INTERCEPT = "links"`.

    A navigation to the page shown, or one with no origin, renders the page instead.
    """
    if router_settings().intercept != InterceptMode.LINKS:
        return None
    href = target_href(request)
    origin = origin_path(request)
    if origin is None or origin == href:
        return None
    zone = _layer_zone(file_path)
    if zone is None:
        return None
    if partial_intent(request).prefetch:
        return refused_prefetch()
    return stamp(answered(Patches(request).layer_open(zone=zone, href=href)), request)


__all__ = ["intercepted"]
