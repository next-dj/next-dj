"""Standalone zone rendering over the full page context."""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.core.exceptions import PermissionDenied
from django.template import Context as DjangoTemplateContext

from next.pages.manager import page
from next.seeding import seed_collector
from next.static.collector import default_placeholders

from .errors import UnknownZoneError
from .registry import zones_of
from .signals import zone_rendered
from .zone import render_zone_body


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.static import StaticCollector

    from .guards import Recheck
    from .registry import ZoneInfo


_NESTABLE_BATCH: Final = 2


@dataclass(frozen=True, slots=True)
class ZoneRenderResult:
    """Rendered zones plus the assets their bodies collected.

    A morph or replace addresses the wrapped element in `html`, while an append or
    prepend grafts the bare body in `bodies` into the live zone.
    """

    html: dict[str, str]
    bodies: dict[str, str]
    collector: "StaticCollector"

    def url_assets(self) -> "Iterator[tuple[str, str]]":
        """Yield the (kind, url) pair of each collected asset that has a URL."""
        for slot in default_placeholders:
            for static_asset in self.collector.assets_in_slot(slot.name):
                if static_asset.url:
                    yield static_asset.kind, static_asset.url

    def inline_assets(self) -> "Iterator[tuple[str, str]]":
        """Yield the (kind, inline body) of each collected inline asset."""
        for slot in default_placeholders:
            for static_asset in self.collector.assets_in_slot(slot.name):
                if static_asset.inline is not None:
                    yield static_asset.kind, static_asset.inline

    def js_context_delta(self) -> dict[str, object]:
        """Return the zone js-context as wire-ready values for a context patch."""
        return self.collector.js_context_wire()


def render_zone(
    page_path: "Path",
    zone_names: tuple[str, ...],
    request: "HttpRequest",
    url_kwargs: dict[str, object] | None = None,
    overrides: dict[str, object] | None = None,
    *,
    context_data: dict[str, object] | None = None,
) -> ZoneRenderResult:
    """Render the named zones of a page and the zones nested in them, with full context.

    An unknown name is skipped, an all-unknown batch raises, refusing `has_perm` denies.
    """
    start = time.perf_counter()
    kwargs = url_kwargs or {}
    template = page.composed_template_for(page_path)
    zones = zones_of(template, page_path)
    declared, rechecks = _declared_zone_names(zone_names, zones)
    if rechecks and any(recheck.refuses_user(request) for recheck in rechecks):
        raise PermissionDenied
    rendered_names = _uncovered_zone_names(declared, zones)

    if context_data is None:
        context_data = page.build_render_context(
            page_path,
            request,
            _requested_zones=_context_zone_names(rendered_names, zones),
            **kwargs,
        )
    if overrides:
        context_data.update(overrides)

    collector = seed_collector(page_path, context_data)
    django_context = DjangoTemplateContext(context_data)
    if rechecks and any(
        recheck.refuses(django_context, request) for recheck in rechecks
    ):
        raise PermissionDenied

    html: dict[str, str] = {}
    bodies: dict[str, str] = {}
    for name in rendered_names:
        info = zones[name]
        body, wrapped = render_zone_body(
            info.partial, info.name, info.options, django_context
        )
        # SafeString is a str, kept as-is to avoid copies on the hot path.
        html[name] = wrapped
        bodies[name] = body

    _emit_rendered(page_path, rendered_names, request, start)
    return ZoneRenderResult(html=html, bodies=bodies, collector=collector)


def _declared_zone_names(
    zone_names: tuple[str, ...], zones: "Mapping[str, ZoneInfo]"
) -> "tuple[tuple[str, ...], list[Recheck]]":
    """Return the declared names of a batch in request order, with their rechecks.

    An undeclared or bound name is dropped, and an empty result raises.
    """
    declared: list[str] = []
    rechecks: list[Recheck] = []
    for name in dict.fromkeys(zone_names):
        info = zones.get(name)
        if info is None or not info.standalone:
            continue
        declared.append(name)
        if info.recheck is not None:
            rechecks.append(info.recheck)
    if zone_names and not declared:
        open_names = sorted(name for name, info in zones.items() if info.standalone)
        raise UnknownZoneError(zone_names[0], tuple(open_names))
    return tuple(declared), rechecks


def _uncovered_zone_names(
    declared: tuple[str, ...], zones: "Mapping[str, ZoneInfo]"
) -> tuple[str, ...]:
    """Return the declared names no other name of the batch renders in its body."""
    if len(declared) < _NESTABLE_BATCH:
        return declared
    covered = frozenset[str]().union(*(zones[name].nested for name in declared))
    return tuple(name for name in declared if name not in covered)


def _context_zone_names(
    rendered: tuple[str, ...], zones: "Mapping[str, ZoneInfo]"
) -> frozenset[str]:
    """Return the batch names widened by the zones nested in their bodies.

    A nested zone renders inside the body of a requested one, so its
    zone-bound context callables have to run for the batch.
    """
    widened = set(rendered)
    for name in rendered:
        widened |= zones[name].nested
    return frozenset(widened)


def _emit_rendered(
    page_path: "Path", zone_names: tuple[str, ...], request: "HttpRequest", start: float
) -> None:
    """Announce each rendered zone, timing the render only for a listener."""
    if not zone_rendered.has_listeners(ZoneRenderResult):
        return
    duration_ms = (time.perf_counter() - start) * 1000
    for name in zone_names:
        zone_rendered.send(
            sender=ZoneRenderResult,
            zone_name=name,
            page_path=page_path,
            request=request,
            duration_ms=duration_ms,
        )


__all__ = ["ZoneRenderResult", "render_zone"]
