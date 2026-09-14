"""Shaping of form action outcomes into patch envelopes for partial requests."""

from .outcomes import drain_messages, shape_partial
from .validate import ActionRef, shape_validate


__all__ = ["ActionRef", "drain_messages", "shape_partial", "shape_validate"]
