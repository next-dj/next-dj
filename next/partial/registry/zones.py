"""Registry of the zones one compiled page template declares."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast
from weakref import WeakKeyDictionary

from next.components.manager import components_manager
from next.partial.guards import (
    Components,
    Recheck,
    Stamps,
    bound_zones,
    fresh,
    rechecks,
)
from next.partial.headers import partial_intent
from next.partial.signals import zone_registered
from next.partial.zone import ZoneNode
from next.utils import template_edits_watched


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest
    from django.template.base import Template

    from next.partial.zone import ZoneOptions, ZonePartial


@dataclass(frozen=True, slots=True)
class ZoneInfo:
    """One compiled zone of a composed page template.

    `nested`, `standalone` and the `{% has_perm %}` blocks above it are computed once.
    """

    name: str
    partial: "ZonePartial"
    options: "ZoneOptions"
    nested: frozenset[str]
    standalone: bool = True
    recheck: Recheck | None = None

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


def _bound(template: "Template", page_path: "Path") -> tuple[frozenset[str], Stamps]:
    """Return the zones a standalone render refuses and the files that answer read."""
    components = Components(components_manager, page_path)
    return bound_zones(template.nodelist, components), components.stamps


def _zones_from_template(template: "Template", page_path: "Path") -> _Entry:
    """Walk a compiled template once and index its zones by name."""
    zones: dict[str, ZoneInfo] = {}
    nodes = cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))
    bound, stamps = _bound(template, page_path) if nodes else (frozenset(), frozenset())
    checks = rechecks(template.nodelist) if nodes else {}
    for node in nodes:
        zones[node.name] = ZoneInfo(
            name=node.name,
            partial=node.partial,
            options=node.options,
            nested=_nested_names(node.partial),
            standalone=node.name not in bound,
            recheck=checks.get(node.name),
        )
    return _Entry(zones, stamps)


def _nested_names(partial: "ZonePartial") -> frozenset[str]:
    """Return the names of the zones declared inside the body of a zone.

    The walk starts at the body, so the placeholder branch stays out, and
    Django recurses through the child node lists, so any depth is covered.
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


def zone_requested(request: "HttpRequest", name: str) -> bool:
    """Return True when the partial intent of the request names the zone."""
    return name in partial_intent(request).zones


__all__ = ["ZoneInfo", "zone_requested", "zones_of"]
