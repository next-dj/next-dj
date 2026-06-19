"""Registries for patch verbs and the zones of a compiled page template."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from weakref import WeakKeyDictionary

from .headers import partial_intent
from .markers import ZoneNode
from .signals import patch_op_registered, zone_registered


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest
    from django.template.base import Template

    from .markers import ZonePartial


BUILTIN_OPS: frozenset[str] = frozenset(
    {
        "morph",
        "replace",
        "inner",
        "append",
        "prepend",
        "remove",
        "refresh",
        "context",
        "event",
        "toast",
        "layer.open",
        "layer.close",
        "url",
        "visit",
    }
)


class PatchOpRegistry:
    """Registry of patch verbs known to the builder.

    The built-in verbs seed the registry so the core eats its own dog
    food. A project registers a custom verb to clear the `next.E066`
    check and earn the generic `op()` channel on the builder.
    """

    def __init__(self) -> None:
        """Seed the registry with the built-in verbs."""
        self._ops: set[str] = set(BUILTIN_OPS)
        self._custom: set[str] = set()

    def register(self, name: str) -> None:
        """Register a custom verb and announce it to subscribers."""
        self._ops.add(name)
        if name not in BUILTIN_OPS:
            self._custom.add(name)
        if patch_op_registered.receivers:
            patch_op_registered.send(sender=type(self), name=name)

    def __contains__(self, name: object) -> bool:
        """Return True when the verb is known to the registry."""
        return name in self._ops

    def custom_names(self) -> frozenset[str]:
        """Return the verb names registered beyond the built-in set."""
        return frozenset(self._custom)


patch_op_registry = PatchOpRegistry()


def register_patch_op(name: str) -> None:
    """Register a custom patch verb with the builder side of the protocol."""
    patch_op_registry.register(name)


@dataclass(frozen=True, slots=True)
class ZoneInfo:
    """One compiled zone of a composed page template."""

    name: str
    lazy: str | None
    tag: str
    partial: "ZonePartial"


_zone_cache: "WeakKeyDictionary[Template, Mapping[str, ZoneInfo]]" = WeakKeyDictionary()


def _zones_from_template(template: "Template") -> dict[str, ZoneInfo]:
    """Walk a compiled template once and index its zones by name."""
    zones: dict[str, ZoneInfo] = {}
    nodes = cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))
    for node in nodes:
        zones[node.name] = ZoneInfo(
            name=node.name,
            lazy=node.lazy,
            tag=node.tag,
            partial=node.partial,
        )
    return zones


def zones_of(template: "Template") -> "Mapping[str, ZoneInfo]":
    """Return the named zones of a compiled template, memoised per object.

    The cache keys on the compiled template object, so a recompiled
    page gets a fresh entry while the stale object is collected. The
    first read of a template announces its zones through
    `zone_registered`.
    """
    cached = _zone_cache.get(template)
    if cached is not None:
        return cached
    zones = _zones_from_template(template)
    _zone_cache[template] = zones
    if zone_registered.receivers:
        for info in zones.values():
            zone_registered.send(
                sender=type(template),
                template=template,
                zone_name=info.name,
                lazy=info.lazy,
            )
    return zones


def zone_requested(request: "HttpRequest", name: str) -> bool:
    """Return True when the partial intent of the request names the zone."""
    return name in partial_intent(request).zones


__all__ = [
    "BUILTIN_OPS",
    "PatchOpRegistry",
    "ZoneInfo",
    "patch_op_registry",
    "register_patch_op",
    "zone_requested",
    "zones_of",
]
