"""Public facade for the partial-rendering subsystem."""

from . import checks, signals
from .backends import JsonPartialProtocolBackend, PartialProtocolBackend
from .envelope import Asset, Envelope, FormMeta, Patch
from .errors import (
    ForeignPageNotAuthorizedError,
    LayerHrefWithoutZoneError,
    OriginUnavailableError,
    UnknownZoneError,
)
from .headers import is_partial_request, partial_intent
from .origin import resolve_partial_origin
from .paging import KeysetPager, NumberPager, PagedRows, Pager, Paging, Plan, Window
from .patches import Patches, PatchResponse
from .registry import register_patch_op, zone_requested
from .render import ZoneRenderResult, render_zone
from .shaping import shape_partial
from .shaping.shaper import FormShaper
from .sse import PatchEventStream
from .stream import StreamMeta


__all__ = [
    "Asset",
    "Envelope",
    "ForeignPageNotAuthorizedError",
    "FormMeta",
    "FormShaper",
    "JsonPartialProtocolBackend",
    "KeysetPager",
    "LayerHrefWithoutZoneError",
    "NumberPager",
    "OriginUnavailableError",
    "PagedRows",
    "Pager",
    "Paging",
    "PartialProtocolBackend",
    "Patch",
    "PatchEventStream",
    "PatchResponse",
    "Patches",
    "Plan",
    "StreamMeta",
    "UnknownZoneError",
    "Window",
    "ZoneRenderResult",
    "checks",
    "is_partial_request",
    "partial_intent",
    "register_patch_op",
    "render_zone",
    "resolve_partial_origin",
    "shape_partial",
    "signals",
    "zone_requested",
]
