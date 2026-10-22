"""Registry of the zones one compiled page template declares."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, cast
from weakref import WeakKeyDictionary

from django.template import Template, TemplateDoesNotExist, TemplateSyntaxError

from next.components.manager import components_manager
from next.pages import page
from next.pages.loaders import LayoutTemplateLoader
from next.partial.guards import Components, Stamps, bound_zones, fresh
from next.partial.headers import partial_intent
from next.partial.signals import zone_registered
from next.partial.zone import ZoneNode
from next.utils import template_edits_watched


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.partial.zone import ZoneOptions, ZonePartial


@dataclass(frozen=True, slots=True)
class ZoneInfo:
    """One compiled zone of a composed page template.

    Properties delegate to `options`, and `nested` and `standalone` are read once.
    """

    name: str
    partial: "ZonePartial"
    options: "ZoneOptions"
    nested: frozenset[str]
    standalone: bool = True

    @property
    def lazy(self) -> str | None:
        """Lazy trigger of the zone, read from its options."""
        return self.options.lazy

    @property
    def poll(self) -> int | None:
        """Poll interval of the zone in milliseconds, read from its options."""
        return self.options.poll

    @property
    def tag(self) -> str:
        """Wrapper tag name of the zone, read from its options."""
        return self.options.tag


@dataclass(frozen=True, slots=True)
class _Entry:
    zones: "Mapping[str, ZoneInfo]"
    stamps: Stamps


_zone_cache: "WeakKeyDictionary[Template, _Entry]" = WeakKeyDictionary()
_layout_cache: "WeakKeyDictionary[Template, frozenset[str]]" = WeakKeyDictionary()


def _bound(template: "Template", page_path: "Path") -> tuple[frozenset[str], Stamps]:
    """Return the zones a standalone render refuses and the files that answer read."""
    components = Components(components_manager, page_path)
    return bound_zones(template.nodelist, components), components.stamps


def _zones_from_template(template: "Template", page_path: "Path") -> _Entry:
    """Walk a compiled template once and index its zones by name."""
    zones: dict[str, ZoneInfo] = {}
    nodes = cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))
    bound, stamps = _bound(template, page_path) if nodes else (frozenset(), frozenset())
    for node in nodes:
        zones[node.name] = ZoneInfo(
            name=node.name,
            partial=node.partial,
            options=node.options,
            nested=_nested_names(node.partial),
            standalone=node.name not in bound,
        )
    return _Entry(zones, stamps)


def _nested_names(partial: "ZonePartial") -> frozenset[str]:
    """Return the names of the zones declared inside the body of a zone.

    The walk starts at the body and Django recurses through child node lists.
    """
    inner = cast("list[ZoneNode]", partial.nodelist.get_nodes_by_type(ZoneNode))
    return frozenset(child.name for child in inner)


def zones_of(template: "Template", page_path: "Path") -> "Mapping[str, ZoneInfo]":
    """Return the named zones of the composed template of `page_path`, memoised.

    A first read announces the zones, and a process watching edits rereads components.
    """
    cached = _zone_cache.get(template)
    if cached is not None:
        if not cached.stamps or not template_edits_watched() or fresh(cached.stamps):
            return cached.zones
        bound, stamps = _bound(template, page_path)
        rebuilt = {
            name: replace(info, standalone=name not in bound)
            for name, info in cached.zones.items()
        }
        _zone_cache[template] = _Entry(rebuilt, stamps)
        return rebuilt
    entry = _zones_from_template(template, page_path)
    _zone_cache[template] = entry
    zones = entry.zones
    sender = type(template)
    for info in zones.values():
        zone_registered.send(
            sender=sender,
            template=template,
            zone_name=info.name,
            lazy=info.options.lazy,
            poll=info.options.poll,
        )
    return zones


COMPOSE_FAILURES: Final = (
    TemplateSyntaxError,
    TemplateDoesNotExist,
    OSError,
    ValueError,
)
"""What composing a page template raises, for a reader that tolerates a broken page."""


def standalone_zones(page_path: "Path") -> tuple[str, ...]:
    """Return the zones of a page that render on their own, in declaration order.

    A zone inside a component that binds it is left out, as no zone request serves it.
    """
    declared = zones_of(page.composed_template_for(page_path), page_path)
    return tuple(name for name, info in declared.items() if info.standalone)


def _composed(page_path: "Path") -> Template | None:
    """Return the composed template of a page, `None` where it does not compose."""
    try:
        return page.composed_template_for(page_path)
    except COMPOSE_FAILURES:
        return None


def _layout_zone_names(template: Template, page_path: "Path") -> frozenset[str]:
    """Return the zones the layouts around a page declare.

    A read is kept with the composed template, which a layout edit replaces.
    """
    cached = _layout_cache.get(template)
    if cached is not None:
        return cached
    loader = LayoutTemplateLoader()
    if not loader.can_load(page_path):
        return frozenset()
    skeleton = Template(loader.compose_skeleton(page_path))
    nodes = skeleton.nodelist.get_nodes_by_type(ZoneNode)
    names = frozenset(node.name for node in nodes if isinstance(node, ZoneNode))
    _layout_cache[template] = names
    return names


def own_zones(page_path: "Path") -> "Mapping[str, ZoneInfo] | None":
    """Return the zones the body of a page declares, `None` where it does not compose.

    The zones of its layouts are left out, since the page below shares them.
    """
    template = _composed(page_path)
    if template is None:
        return None
    layouts = _layout_zone_names(template, page_path)
    declared = zones_of(template, page_path)
    return {name: info for name, info in declared.items() if name not in layouts}


def own_standalone_zones(page_path: "Path") -> tuple[str, ...]:
    """Return the standalone zones of a page body, none where it does not compose."""
    zones = own_zones(page_path) or {}
    return tuple(name for name, info in zones.items() if info.standalone)


def target_zones(page_path: "Path") -> frozenset[str]:
    """Return the standalone zones of a target, none where it does not compose."""
    template = _composed(page_path)
    if template is None:
        return frozenset()
    declared = zones_of(template, page_path)
    return frozenset(name for name, info in declared.items() if info.standalone)


def zones_served(page_path: "Path", target_file: "Path") -> tuple[str, ...]:
    """Return the zones an intercept page serves over its target, in body order.

    The target keeps any zone it declares, and a layout of the intercept never renders.
    """
    kept = target_zones(target_file)
    return tuple(name for name in own_standalone_zones(page_path) if name not in kept)


def zone_requested(request: "HttpRequest", name: str) -> bool:
    """Return True when the partial intent of the request names the zone."""
    return name in partial_intent(request).zones


__all__ = [
    "COMPOSE_FAILURES",
    "ZoneInfo",
    "own_standalone_zones",
    "own_zones",
    "standalone_zones",
    "target_zones",
    "zone_requested",
    "zones_of",
    "zones_served",
]
