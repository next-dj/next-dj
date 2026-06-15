"""Public facade for the partial-rendering subsystem."""

from . import signals
from .backends import PartialProtocolBackend, partial_backend_manager
from .headers import PartialIntent, is_partial_request, partial_intent
from .patches import (
    Asset,
    DeferZone,
    Envelope,
    FormMeta,
    Patch,
    Patches,
    PatchResponse,
)
from .registry import ZoneInfo, register_patch_op, zone_requested, zones_of


__all__ = [
    "Asset",
    "DeferZone",
    "Envelope",
    "FormMeta",
    "PartialIntent",
    "PartialProtocolBackend",
    "Patch",
    "PatchResponse",
    "Patches",
    "ZoneInfo",
    "is_partial_request",
    "partial_backend_manager",
    "partial_intent",
    "register_patch_op",
    "signals",
    "zone_requested",
    "zones_of",
]
