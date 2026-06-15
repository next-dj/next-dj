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
from .registry import register_patch_op


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
    "is_partial_request",
    "partial_backend_manager",
    "partial_intent",
    "register_patch_op",
    "signals",
]
