"""Registries for patch verbs and the zones of a compiled page template.

Two unrelated records share the address, so each keeps its own submodule.
"""

from .ops import BUILTIN_OPS, PatchOpRegistry, patch_op_registry, register_patch_op
from .zones import (
    COMPOSE_FAILURES,
    ZoneInfo,
    standalone_zones,
    zone_requested,
    zones_of,
)


__all__ = [
    "BUILTIN_OPS",
    "COMPOSE_FAILURES",
    "PatchOpRegistry",
    "ZoneInfo",
    "patch_op_registry",
    "register_patch_op",
    "standalone_zones",
    "zone_requested",
    "zones_of",
]
