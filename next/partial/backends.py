"""Pluggable protocol-backend contract and its JSON wire format implementation."""

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from . import keys
from .envelope import Envelope
from .headers import CONTENT_TYPE
from .stream import StreamMeta


if TYPE_CHECKING:
    from collections.abc import Mapping


_SSE_EVENT_NAME = "next-patches"


def _sse_json_event(name: str, data: object) -> str:
    """Encode one control event using compact JSON and standard SSE framing."""
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


def _stream_frame(envelope: "Envelope", meta: StreamMeta) -> dict[str, Any]:
    """Merge the stream metadata into the envelope's JSON object."""
    data = envelope.as_dict()
    data.update(meta.as_dict())
    return data


class PartialProtocolBackend(ABC):
    """Pluggable strategy for the wire format of patch envelopes.

    The constructor takes the whole `PARTIAL_BACKENDS` entry and exposes its OPTIONS.
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

    def sse_stream_event(self, envelope: "Envelope", meta: StreamMeta) -> str:
        """Serialize one topic frame, dropping the metadata in this fallback.

        A non-JSON codec overrides it to carry `meta`, or clients lose gap detection.
        """
        del meta
        return self.sse_event(envelope)

    def stream_data(
        self, envelope: "Envelope", meta: StreamMeta
    ) -> dict[str, Any] | None:
        """Return a topic frame as a JSON object, or None for a text-only codec.

        The WebSocket transport sends the object as is, and the SSE data text on None.
        """
        del envelope, meta
        return None

    def sse_hello(self, frame: "Mapping[str, object]") -> str:
        """Serialize a stream handshake control frame in the fixed JSON schema."""
        return _sse_json_event(keys.SSE_HELLO, frame)

    def sse_skip(self, frame: "Mapping[str, object]") -> str:
        """Serialize a filtered-message acknowledgment in the fixed JSON schema."""
        return _sse_json_event(keys.SSE_SKIP, frame)

    def sse_end(self, frame: "Mapping[str, object]") -> str:
        """Serialize the terminal stream frame in the fixed JSON schema."""
        return _sse_json_event(keys.SSE_END, frame)

    @abstractmethod
    def deserialize_envelope(self, body: bytes) -> "Envelope":
        """Rebuild one envelope from a response body, the inverse of serializing.

        A reader parses a body through the backend that wrote it.
        """


def _custom_sse_event(cls: type["JsonPartialProtocolBackend"]) -> bool:
    """Tell whether a JSON backend subclass overrides `sse_event` with its own shape."""
    return cls.sse_event is not JsonPartialProtocolBackend.sse_event


class JsonPartialProtocolBackend(PartialProtocolBackend):
    """Serialize envelopes as compact JSON under the next.dj patch MIME type.

    The response body and the SSE data line carry the same JSON envelope.
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

    @override
    def sse_stream_event(self, envelope: "Envelope", meta: StreamMeta) -> str:
        """Merge stream metadata into the JSON frame unless `sse_event` is overridden.

        An overridden `sse_event` defines its own SSE shape, so that shape wins.
        """
        if _custom_sse_event(type(self)):
            return super().sse_stream_event(envelope, meta)
        return _sse_json_event(_SSE_EVENT_NAME, _stream_frame(envelope, meta))

    @override
    def stream_data(
        self, envelope: "Envelope", meta: StreamMeta
    ) -> dict[str, Any] | None:
        """Return the SSE frame object, or None under a custom MIME type or SSE shape.

        A custom MIME type keys its own client parse hook, which only reads text frames.
        """
        cls = type(self)
        if (
            self.content_type != CONTENT_TYPE
            or _custom_sse_event(cls)
            or cls.sse_stream_event is not JsonPartialProtocolBackend.sse_stream_event
        ):
            return None
        return _stream_frame(envelope, meta)

    @override
    def deserialize_envelope(self, body: bytes) -> "Envelope":
        """Rebuild one envelope from a compact JSON response body."""
        return Envelope.from_dict(json.loads(body.decode("utf-8")))


__all__ = ["JsonPartialProtocolBackend", "PartialProtocolBackend"]
