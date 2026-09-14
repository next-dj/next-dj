"""Registries for patch verbs and the zones of a compiled page template."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from weakref import WeakKeyDictionary

from .headers import partial_intent
from .signals import patch_op_registered, zone_registered
from .zone import ZoneNode


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest
    from django.template.base import Template

    from .zone import ZoneOptions, ZonePartial


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

    Registering a verb clears the `next.E066` check and unlocks the `op()` channel.
    """

    def __init__(self) -> None:
        """Start with no custom verb on record, the built-ins seed the reads."""
        self._custom: set[str] = set()

    def register(self, name: str) -> None:
        """Register a custom verb and announce it to subscribers.

        The name is recorded whatever it is, so a registration shadowing a
        built-in verb stays visible to the check that reports it.
        """
        self._custom.add(name)
        patch_op_registered.send(sender=type(self), name=name)

    def __contains__(self, name: object) -> bool:
        """Return True when the verb is built in or registered by a project."""
        return name in BUILTIN_OPS or name in self._custom

    def custom_names(self) -> frozenset[str]:
        """Return every verb name a project registered itself."""
        return frozenset(self._custom)


patch_op_registry = PatchOpRegistry()


def register_patch_op(name: str) -> None:
    """Register a custom patch verb with the builder side of the protocol."""
    patch_op_registry.register(name)


@dataclass(frozen=True, slots=True)
class ZoneInfo:
    """One compiled zone of a composed page template.

    Scalar properties delegate to `options` so no mode can drift between the two reads.
    `nested` is computed once so a standalone render never re-walks the nodes.
    """

    name: str
    partial: "ZonePartial"
    options: "ZoneOptions"
    nested: frozenset[str]

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


_zone_cache: "WeakKeyDictionary[Template, Mapping[str, ZoneInfo]]" = WeakKeyDictionary()


def _zones_from_template(template: "Template") -> dict[str, ZoneInfo]:
    """Walk a compiled template once and index its zones by name."""
    zones: dict[str, ZoneInfo] = {}
    nodes = cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))
    for node in nodes:
        zones[node.name] = ZoneInfo(
            name=node.name,
            partial=node.partial,
            options=node.options,
            nested=_nested_names(node.partial),
        )
    return zones


def _nested_names(partial: "ZonePartial") -> frozenset[str]:
    """Return the names of the zones declared inside the body of a zone.

    The walk starts at the body, so the placeholder branch stays out, and
    Django recurses through the child node lists, so any depth is covered.
    """
    inner = cast("list[ZoneNode]", partial.nodelist.get_nodes_by_type(ZoneNode))
    return frozenset(child.name for child in inner)


def zones_of(template: "Template") -> "Mapping[str, ZoneInfo]":
    """Return the named zones of a compiled template, memoised per object.

    The cache keys on the compiled template object, so a recompiled page gets a fresh
    entry and the first read announces its zones through `zone_registered`.
    """
    cached = _zone_cache.get(template)
    if cached is not None:
        return cached
    zones = _zones_from_template(template)
    _zone_cache[template] = zones
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


__all__ = [
    "BUILTIN_OPS",
    "PatchOpRegistry",
    "ZoneInfo",
    "patch_op_registry",
    "register_patch_op",
    "zone_requested",
    "zones_of",
]
