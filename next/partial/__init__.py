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
from .render import UnknownZoneError, ZoneRenderResult, render_zone
from .shaping import drain_messages, shape_partial


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
    "UnknownZoneError",
    "ZoneInfo",
    "ZoneRenderResult",
    "drain_messages",
    "is_partial_request",
    "partial_backend_manager",
    "partial_intent",
    "register_patch_op",
    "render_zone",
    "shape_partial",
    "signals",
    "zone_requested",
    "zones_of",
]
