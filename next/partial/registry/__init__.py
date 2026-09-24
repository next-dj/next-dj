"""Registries for patch verbs and the zones of a compiled page template.

Two unrelated records share the address, so each keeps its own submodule.
"""

from .ops import BUILTIN_OPS, PatchOpRegistry, patch_op_registry, register_patch_op
from .zones import ZoneInfo, zone_requested, zones_of


__all__ = [
    "BUILTIN_OPS",
    "PatchOpRegistry",
    "ZoneInfo",
    "patch_op_registry",
    "register_patch_op",
    "zone_requested",
    "zones_of",
]
