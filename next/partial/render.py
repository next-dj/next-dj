"""Standalone zone rendering over the page context its zones ask for."""

import functools
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Final

from django.template import Context as DjangoTemplateContext

from next.caches import PageCache
from next.diagnostics import degraded_status
from next.pages.boundaries import Caught, boundary_scope
from next.pages.manager import defers, page
from next.ports import render_deferred
from next.seeding import seed_collector
from next.static.collector import default_placeholders
from next.templatetags.boundaries import (
    PageBoundaryNode,
    contained_by,
    enclosing,
    failed_by,
)

from .errors import UnknownZoneError
from .registry import zones_of
from .signals import zone_rendered
from .zone import render_zone_body


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from django.http import HttpRequest
    from django.template import Template

    from next.pages.loaders import BoundaryChain
    from next.static import StaticCollector

    from .registry import ZoneInfo


_NESTABLE_BATCH: Final = 2


@dataclass(frozen=True, slots=True)
class ZoneRenderResult:
    """Rendered zones plus the assets their bodies collected.

    `html` holds the wrapped element for morph or replace, `bodies` the bare body.
    A `failed` result holds the error UI of a page boundary, which only a morph places.
    """

    html: dict[str, str]
    bodies: dict[str, str]
    collector: "StaticCollector"
    context: dict[str, object] = field(default_factory=dict)
    failed: bool = False

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


_ENCLOSING: PageCache[tuple["Template", str], tuple[PageBoundaryNode, ...]] = (
    PageCache()
)


def render_zone(
    page_path: "Path",
    zone_names: tuple[str, ...],
    request: "HttpRequest",
    url_kwargs: dict[str, object] | None = None,
    overrides: dict[str, object] | None = None,
    caught: Caught | None = None,
    *,
    stashed: bool = False,
) -> ZoneRenderResult:
    """Render the named zones of a page over the context their batch asks for.

    A nested zone runs its `@context(zone=)` too, and a batch of unknown names raises.
    A failure a page boundary around the zones contains answers with its error UI.
    A `stashed` batch carries each zone's own skeleton, for a document that lacks it.
    """
    template = page.composed_template_for(page_path)
    chain = page.composed_chain(page_path)
    zones = zones_of(template, page_path)
    try:
        rendered_names = _renderable_zone_names(zone_names, zones)
    except UnknownZoneError:
        if caught is None:
            raise
        raise caught.error from None
    requested = _context_zone_names(rendered_names, zones)
    with boundary_scope(chain):
        if caught is not None:
            built: dict[str, object] | Caught = caught
        elif chain.errors:
            built = page.guarded_context(
                page_path, request, url_kwargs, chain=chain, requested_zones=requested
            )
        else:
            built = page.build_render_context(
                page_path, request, url_kwargs, requested_zones=requested
            )
        if isinstance(built, Caught):
            return _failed_zones(
                page_path, template, zones, rendered_names, built, request, chain
            )
        if overrides:
            built.update(overrides)
        infos = tuple(zones[name] for name in rendered_names)
        return _rendered_zones(
            page_path, template, infos, built, request, chain, stashed=stashed
        )


def render_plain_zones(
    page_path: "Path",
    template: "Template",
    zone_names: tuple[str, ...],
    request: "HttpRequest",
    url_kwargs: dict[str, object] | None,
) -> ZoneRenderResult:
    """Render the named zones of a page that has no boundary and defers nothing.

    `template` is its compiled template, which the caller already holds.
    """
    zones = zones_of(template, page_path)
    rendered_names = _renderable_zone_names(zone_names, zones)
    built = page.build_render_context(
        page_path,
        request,
        url_kwargs,
        requested_zones=_context_zone_names(rendered_names, zones),
    )
    infos = tuple(zones[name] for name in rendered_names)
    return _rendered_zones(
        page_path, template, infos, built, request, None, stashed=False
    )


def _rendered_zones(
    page_path: "Path",
    template: "Template",
    infos: tuple["ZoneInfo", ...],
    context_data: dict[str, object],
    request: "HttpRequest",
    chain: "BoundaryChain | None",
    *,
    stashed: bool,
) -> ZoneRenderResult:
    """Render each zone body, inside the page boundaries enclosing it.

    No `chain` stands for a plain page, which has no boundary and defers nothing.
    """
    collector = seed_collector(page_path, context_data)
    django_context = DjangoTemplateContext(context_data)

    listening = zone_rendered.has_listeners(ZoneRenderResult)
    deferred = chain is not None and defers(chain)
    errors = chain is not None and bool(chain.errors)
    html: dict[str, str] = {}
    bodies: dict[str, str] = {}
    durations: dict[str, float] = {}
    for info in infos:
        name = info.name
        start = perf_counter() if listening else 0.0
        nodes = _enclosing(template, info) if errors else ()
        around = (
            functools.partial(contained_by, nodes, django_context) if nodes else None
        )
        if deferred:
            around = functools.partial(_assembled, around, collector, request)
        body, wrapped = render_zone_body(
            info.partial,
            name,
            info.options,
            django_context,
            page_template=template,
            around=around,
            stashed=stashed,
        )
        if listening:
            durations[name] = (perf_counter() - start) * 1000
        # SafeString is a str, kept as-is to avoid copies on the hot path.
        html[name] = wrapped
        bodies[name] = body

    _emit_rendered(page_path, durations, request)
    return ZoneRenderResult(
        html=html,
        bodies=bodies,
        collector=collector,
        context=context_data,
        failed=errors and degraded_status() is not None,
    )


def _assembled(
    around: "Callable[[Callable[[], str]], str] | None",
    collector: "StaticCollector",
    request: "HttpRequest",
    render: "Callable[[], str]",
) -> str:
    """Render one zone body inside `around`, its deferred calls run and settled in."""
    inner = render if around is None else functools.partial(around, render)
    return render_deferred(inner, collector=collector, request=request)


def _failed_zones(
    page_path: "Path",
    template: "Template",
    zones: "Mapping[str, ZoneInfo]",
    rendered_names: tuple[str, ...],
    caught: Caught,
    request: "HttpRequest",
    chain: "BoundaryChain",
) -> ZoneRenderResult:
    """Render the error UI of the boundary that caught into every zone of the batch.

    A zone outside that boundary has no error UI for it, so the failure goes to Django.
    """
    source = caught.boundary.source
    outward: tuple[PageBoundaryNode, ...] = ()
    for name in rendered_names:
        outward = _outward(_enclosing(template, zones[name]), source)
        if not outward:
            raise caught.error
    context_data = page.caught_context(
        page_path, caught, request, {}, dep_cache=None, chain=chain
    )
    collector = seed_collector(page_path, context_data)
    django_context = DjangoTemplateContext(context_data)
    shown = failed_by(outward, django_context, caught.error)
    html: dict[str, str] = {}
    bodies: dict[str, str] = {}
    for name in rendered_names:
        info = zones[name]
        body, wrapped = render_zone_body(
            info.partial,
            info.name,
            info.options,
            django_context,
            around=functools.partial(_constant, shown),
        )
        html[name] = wrapped
        bodies[name] = body
    return ZoneRenderResult(
        html=html, bodies=bodies, collector=collector, context=context_data, failed=True
    )


def _enclosing(template: "Template", info: "ZoneInfo") -> tuple[PageBoundaryNode, ...]:
    """Return the page boundaries around one zone of `template`, innermost first."""
    key = (template, info.name)
    held = _ENCLOSING.get(key)
    if held is None:
        held = enclosing(template.nodelist, info.partial.nodelist) or ()
        _ENCLOSING[key] = held
    return held


def _outward(
    nodes: tuple[PageBoundaryNode, ...], source: "Path"
) -> tuple[PageBoundaryNode, ...]:
    """Return `nodes` from the boundary of `source` outward, empty when none is it."""
    for index, node in enumerate(nodes):
        if node.source == source:
            return nodes[index:]
    return ()


def _constant(shown: str, _render: "Callable[[], str]") -> str:
    """Return the error UI already rendered, in place of the zone body."""
    return shown


def _renderable_zone_names(
    zone_names: tuple[str, ...], zones: "Mapping[str, ZoneInfo]"
) -> tuple[str, ...]:
    """Return the declared names of a batch, deduplicated in request order.

    An undeclared, bound or nested name drops out, and a batch left empty raises.
    """
    declared = tuple(
        name
        for name in dict.fromkeys(zone_names)
        if (info := zones.get(name)) is not None and info.standalone
    )
    if zone_names and not declared:
        open_names = sorted(name for name, info in zones.items() if info.standalone)
        raise UnknownZoneError(zone_names[0], tuple(open_names))
    if len(declared) < _NESTABLE_BATCH:
        return declared
    covered = frozenset[str]().union(*(zones[name].nested for name in declared))
    return tuple(name for name in declared if name not in covered)


def _context_zone_names(
    rendered: tuple[str, ...], zones: "Mapping[str, ZoneInfo]"
) -> frozenset[str]:
    """Return the batch names widened by the zones nested in their bodies.

    A nested zone renders inside a requested one, so its zone-bound context must run.
    """
    widened = set(rendered)
    for name in rendered:
        widened |= zones[name].nested
    return frozenset(widened)


def _emit_rendered(
    page_path: "Path", durations: "Mapping[str, float]", request: "HttpRequest"
) -> None:
    """Announce each rendered zone with the time its own body took to render."""
    for name, duration_ms in durations.items():
        zone_rendered.send(
            sender=ZoneRenderResult,
            zone_name=name,
            page_path=page_path,
            request=request,
            duration_ms=duration_ms,
        )


__all__ = ["ZoneRenderResult", "render_plain_zones", "render_zone"]
