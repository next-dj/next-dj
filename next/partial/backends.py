"""Pluggable protocol-backend contract and its JSON wire format implementation."""

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from .headers import CONTENT_TYPE


if TYPE_CHECKING:
    from collections.abc import Mapping

    from .envelope import Envelope


_SSE_EVENT_NAME = "next-patches"


class PartialProtocolBackend(ABC):
    """Pluggable strategy for the wire format of patch envelopes.

    The constructor takes the full `PARTIAL_BACKENDS` entry and exposes its OPTIONS on
    `options`, leaving the wire format to the subclass.
    """

    content_type: str
    """MIME type of a serialized envelope, stamped on every patch response."""

    def __init__(self, config: "Mapping[str, Any] | None" = None) -> None:
        """Store the merged backend config and its options."""
        self._config: Mapping[str, Any] = config or {}
        options = self._config.get("OPTIONS")
        self._options: Mapping[str, Any] = options if isinstance(options, dict) else {}

    @property
    def options(self) -> "Mapping[str, Any]":
        """Return the backend OPTIONS mapping from settings."""
        return self._options

    @abstractmethod
    def serialize_envelope(self, envelope: "Envelope") -> bytes:
        """Serialize one envelope for an HTTP response body."""

    @abstractmethod
    def sse_event(self, envelope: "Envelope") -> str:
        """Serialize one envelope as an SSE event frame."""


class JsonPartialProtocolBackend(PartialProtocolBackend):
    """Serialize envelopes as compact JSON under the next.dj patch MIME type.

    The response body and the SSE data line carry the same JSON envelope, so a client
    reading one wire format reads the other unchanged.
    """

    content_type = CONTENT_TYPE

    def _dumps(self, envelope: "Envelope") -> str:
        """Serialise an envelope to a compact JSON string."""
        return json.dumps(envelope.as_dict(), separators=(",", ":"), ensure_ascii=False)

    @override
    def serialize_envelope(self, envelope: "Envelope") -> bytes:
        """Serialize one envelope for an HTTP response body."""
        return self._dumps(envelope).encode("utf-8")

    @override
    def sse_event(self, envelope: "Envelope") -> str:
        """Serialize one envelope as an SSE event frame."""
        return f"event: {_SSE_EVENT_NAME}\ndata: {self._dumps(envelope)}\n\n"


__all__ = ["JsonPartialProtocolBackend", "PartialProtocolBackend"]
