"""Standalone zone rendering over the full page context."""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.template import Context as DjangoTemplateContext

from next.pages.manager import page
from next.static.manager import default_manager

from .registry import zones_of
from .signals import zone_rendered
from .zone import render_zone_standalone


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest

    from next.static import StaticCollector


class UnknownZoneError(LookupError):
    """Raised when a partial request names a zone the page does not declare.

    The unified view turns this into a 400 before any zone renders, so a
    typo or a stale client never trips a partial render.
    """

    def __init__(self, zone_name: str) -> None:
        """Store the unknown zone name and build a readable message."""
        self.zone_name = zone_name
        super().__init__(f'Unknown zone "{zone_name}".')


@dataclass(frozen=True, slots=True)
class ZoneRenderResult:
    """Rendered zones plus the assets their bodies collected.

    `html` maps each requested zone name to its wrapped marker element.
    `collector` carries the co-located assets the bodies registered so
    the caller can ship a manifest outward, past the no-op inject.
    """

    html: dict[str, str]
    collector: "StaticCollector"


def render_zone(
    page_path: "Path",
    zone_names: tuple[str, ...],
    request: "HttpRequest",
    url_kwargs: dict[str, object] | None = None,
    overrides: dict[str, object] | None = None,
) -> ZoneRenderResult:
    """Render the named zones of a page with the full page context.

    The context is collected once for the whole batch of names through
    `build_render_context` and a fresh collector is seeded by the same
    convention as the canonical render path, so co-located assets of the
    zone bodies are gathered. The manifest travels outward in the result
    rather than through inject, which is a no-op for fragments. An
    unknown zone name raises before any body renders.
    """
    start = time.perf_counter()
    kwargs = url_kwargs or {}
    template = page.composed_template_for(page_path)
    zones = zones_of(template)
    for name in zone_names:
        if name not in zones:
            raise UnknownZoneError(name)

    context_data = page.build_render_context(page_path, request, **kwargs)
    if overrides:
        context_data.update(overrides)

    collector = _seed_collector(page_path, context_data)
    django_context = DjangoTemplateContext(context_data)

    html: dict[str, str] = {}
    for name in zone_names:
        info = zones[name]
        html[name] = str(
            render_zone_standalone(info.partial, info.name, info.tag, django_context)
        )

    _emit_rendered(page_path, zone_names, request, start)
    return ZoneRenderResult(html=html, collector=collector)


def _seed_collector(
    page_path: "Path",
    context_data: dict[str, object],
) -> "StaticCollector":
    """Seed a fresh collector and bind it to the context like the page path.

    The collector is hydrated with the JS context that
    `build_render_context` left behind, page asset discovery runs, and
    the collector is bound under `_static_collector` so component widgets
    and co-located assets of the zone bodies register against it.
    """
    collector: StaticCollector = default_manager.create_collector()
    js_context = context_data.pop("_next_js_context", {})
    js_serializers = context_data.pop("_next_js_context_serializers", {})
    if isinstance(js_context, dict):
        serializers = js_serializers if isinstance(js_serializers, dict) else {}
        for js_key, js_value in js_context.items():
            collector.add_js_context(
                js_key, js_value, serializer=serializers.get(js_key)
            )
    default_manager.discover_page_assets(page_path, collector)
    context_data["_static_collector"] = collector
    return collector


def _emit_rendered(
    page_path: "Path",
    zone_names: tuple[str, ...],
    request: "HttpRequest",
    start: float,
) -> None:
    """Announce each rendered zone when the signal has receivers."""
    if not zone_rendered.receivers:
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


__all__ = ["UnknownZoneError", "ZoneRenderResult", "render_zone"]
