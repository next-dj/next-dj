"""Public facade for the partial-rendering subsystem."""

from . import signals
from .backends import PartialProtocolBackend
from .headers import PartialIntent, is_partial_request, partial_intent
from .manager import partial_backend_manager
from .origin import OriginSource, PartialOrigin, resolve_partial_origin
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
from .shaping import ActionRef, drain_messages, shape_partial, shape_validate
from .sse import PatchEventStream


__all__ = [
    "ActionRef",
    "Asset",
    "Envelope",
    "ForeignPageNotAuthorizedError",
    "FormMeta",
    "OriginSource",
    "PartialIntent",
    "PartialOrigin",
    "PartialProtocolBackend",
    "Patch",
    "PatchEventStream",
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
    "resolve_partial_origin",
    "shape_partial",
    "shape_validate",
    "signals",
    "zone_requested",
    "zones_of",
]
