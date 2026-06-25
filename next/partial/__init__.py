"""Public facade for the partial-rendering subsystem."""

from . import signals
from .backends import PartialProtocolBackend
from .headers import is_partial_request, partial_intent
from .origin import resolve_partial_origin
from .patches import (
    Asset,
    Envelope,
    ForeignPageNotAuthorizedError,
    FormMeta,
    Patch,
    Patches,
    PatchResponse,
)
from .registry import register_patch_op, zone_requested
from .render import UnknownZoneError, ZoneRenderResult, render_zone
from .shaping import shape_partial
from .sse import PatchEventStream


__all__ = [
    "Asset",
    "Envelope",
    "ForeignPageNotAuthorizedError",
    "FormMeta",
    "PartialProtocolBackend",
    "Patch",
    "PatchEventStream",
    "PatchResponse",
    "Patches",
    "UnknownZoneError",
    "ZoneRenderResult",
    "is_partial_request",
    "partial_intent",
    "register_patch_op",
    "render_zone",
    "resolve_partial_origin",
    "shape_partial",
    "signals",
    "zone_requested",
]
