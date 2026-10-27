from collections.abc import Mapping
from typing import Any

from next.partial import (
    Envelope,
    JsonPartialProtocolBackend,
    PartialProtocolBackend,
    StreamMeta,
)


def sse_data_text(frame: str) -> str:
    """Join the data lines of one SSE frame the way an EventSource reads them."""
    return "\n".join(
        line.removeprefix("data:").removeprefix(" ")
        for line in frame.splitlines()
        if line.startswith("data:")
    )


class TextStreamCodec(PartialProtocolBackend):
    """A non-JSON codec whose topic frame carries the position as two data lines."""

    content_type = "text/plain"

    def serialize_envelope(self, envelope: Envelope) -> bytes:
        """Write the envelope version as the whole body."""
        return envelope.version.encode()

    def stream_data(self, envelope: Envelope, meta: StreamMeta) -> str:
        """Carry the topic position as two lines of text."""
        del envelope
        return f"seq={meta.seq}\n{meta.epoch}"

    def deserialize_envelope(self, body: bytes) -> Envelope:
        """Read the envelope version back from the body."""
        return Envelope(version=body.decode())


class TextDataJsonCodec(JsonPartialProtocolBackend):
    """A JSON codec whose own topic frame carries the position as two lines of text."""

    def stream_data(self, envelope: Envelope, meta: StreamMeta) -> str:
        """Carry the topic position as two lines of text."""
        del envelope
        return f"seq={meta.seq}\n{meta.epoch}"


class MimeJsonCodec(JsonPartialProtocolBackend):
    """A JSON codec under its own MIME type, which keys a text-only parse hook."""

    content_type = "application/vnd.example.patches+json"


class InstanceMimeJsonCodec(JsonPartialProtocolBackend):
    """A JSON codec whose MIME type is set per instance from its OPTIONS."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Read the MIME type from OPTIONS onto the instance."""
        super().__init__(config)
        self.content_type = self.options.get(
            "content_type", "application/vnd.example.instance+json"
        )
