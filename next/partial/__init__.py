"""Public facade for the partial-rendering subsystem."""

from . import signals
from .backends import PartialProtocolBackend
from .headers import (
    REQUEST_ID,
    is_partial_request,
    partial_intent,
)
from .origin import OriginSource, resolve_partial_origin
from .patches import (
    Asset,
    Envelope,
    ForeignPageNotAuthorizedError,
    FormMeta,
    Patch,
    Patches,
    PatchResponse,
)
from .registry import ZoneInfo, register_patch_op, zone_requested, zones_of
from .render import UnknownZoneError, ZoneRenderResult, render_zone
from .shaping import shape_partial
from .sse import PatchEventStream


__all__ = [
    "REQUEST_ID",
    "Asset",
    "Envelope",
    "ForeignPageNotAuthorizedError",
    "FormMeta",
    "OriginSource",
    "PartialProtocolBackend",
    "Patch",
    "PatchEventStream",
    "PatchResponse",
    "Patches",
    "UnknownZoneError",
    "ZoneInfo",
    "ZoneRenderResult",
    "is_partial_request",
    "partial_intent",
    "register_patch_op",
    "render_zone",
    "resolve_partial_origin",
    "shape_partial",
    "signals",
    "zone_requested",
    "zones_of",
]
