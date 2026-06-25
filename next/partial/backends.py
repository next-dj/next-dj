"""Protocol backend owning the patch envelope wire format."""

import json
from typing import TYPE_CHECKING, Any

from .headers import CONTENT_TYPE


if TYPE_CHECKING:
    from collections.abc import Mapping

    from .patches import Envelope


_SSE_EVENT_NAME = "next-patches"


class PartialProtocolBackend:
    """Owner of the wire format for patch envelopes.

    The default backend serialises envelopes as a compact JSON envelope
    under `application/vnd.next.patches+json`. A third party may swap the
    wire format, for example to emulate Turbo Streams, by registering a
    different backend through `PARTIAL_BACKENDS` without touching shaping
    or the registries. Both `serialize_envelope` and `sse_event` operate
    over the same JSON envelope.
    """

    content_type: str = CONTENT_TYPE

    def __init__(self, config: "Mapping[str, Any] | None" = None) -> None:
        """Store the merged backend config and its options."""
        self._config: Mapping[str, Any] = config or {}
        options = self._config.get("OPTIONS")
        self._options: Mapping[str, Any] = options if isinstance(options, dict) else {}

    @property
    def options(self) -> "Mapping[str, Any]":
        """Return the backend OPTIONS mapping from settings."""
        return self._options

    def _dumps(self, envelope: "Envelope") -> str:
        """Serialise an envelope to a compact JSON string."""
        return json.dumps(
            envelope.as_dict(),
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def serialize_envelope(self, envelope: "Envelope") -> bytes:
        """Serialize one envelope for an HTTP response body."""
        return self._dumps(envelope).encode("utf-8")

    def sse_event(self, envelope: "Envelope") -> str:
        """Serialize one envelope as an SSE event frame."""
        return f"event: {_SSE_EVENT_NAME}\ndata: {self._dumps(envelope)}\n\n"


__all__ = ["PartialProtocolBackend"]
