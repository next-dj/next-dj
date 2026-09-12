import json

import pytest

from next.partial import (
    Envelope,
    JsonPartialProtocolBackend,
    PartialProtocolBackend,
    Patches,
)
from next.partial.headers import CONTENT_TYPE


def _sample_envelope() -> Envelope:
    return (
        Patches.versioned("9f3c2e1b")
        .replace({"zone": "list"}, "<div></div>")
        .event("saved", {"id": 7})
        .envelope()
    )


class TestSerializeEnvelope:
    """The default backend serialises envelopes as compact JSON bytes."""

    def test_content_type_is_vendor_mime(self) -> None:
        assert JsonPartialProtocolBackend().content_type == CONTENT_TYPE

    def test_serialize_returns_bytes(self) -> None:
        body = JsonPartialProtocolBackend().serialize_envelope(_sample_envelope())
        assert isinstance(body, bytes)

    def test_serialize_is_compact(self) -> None:
        body = JsonPartialProtocolBackend().serialize_envelope(_sample_envelope())
        assert b", " not in body
        assert b": " not in body

    def test_serialize_round_trips(self) -> None:
        body = JsonPartialProtocolBackend().serialize_envelope(_sample_envelope())
        data = json.loads(body)
        assert data["version"] == "9f3c2e1b"
        assert data["ops"][0]["op"] == "replace"
        assert data["ops"][1]["op"] == "event"

    def test_serialize_keeps_non_ascii(self) -> None:
        envelope = Patches.versioned("v1").event("сохранено").envelope()
        body = JsonPartialProtocolBackend().serialize_envelope(envelope)
        assert "сохранено".encode() in body


class TestSseEvent:
    """The SSE frame wraps the same JSON envelope as a `next-patches` event."""

    def test_event_name_and_data(self) -> None:
        frame = JsonPartialProtocolBackend().sse_event(_sample_envelope())
        assert frame.startswith("event: next-patches\n")
        assert frame.endswith("\n\n")

    def test_event_carries_same_json_as_body(self) -> None:
        backend = JsonPartialProtocolBackend()
        envelope = _sample_envelope()
        body = backend.serialize_envelope(envelope).decode()
        frame = backend.sse_event(envelope)
        data_line = frame.splitlines()[1]
        assert data_line == f"data: {body}"


class TestBackendOptions:
    """OPTIONS from the settings entry are exposed on the backend."""

    def test_options_read_from_config(self) -> None:
        backend = JsonPartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
        assert backend.options["VERSION"] == "manifest"

    def test_options_default_empty(self) -> None:
        assert JsonPartialProtocolBackend().options == {}

    def test_non_dict_options_falls_back_to_empty(self) -> None:
        backend = JsonPartialProtocolBackend({"OPTIONS": "bogus"})
        assert backend.options == {}


class _TurboProtocolBackend(PartialProtocolBackend):
    content_type = "text/vnd.turbo-stream.html"

    def serialize_envelope(self, envelope: Envelope) -> bytes:
        return str(len(envelope.ops)).encode()

    def sse_event(self, envelope: Envelope) -> str:
        return f"event: turbo\ndata: {len(envelope.ops)}\n\n"


class TestProtocolContract:
    """The family root is abstract and names the wire format contract."""

    def test_root_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            PartialProtocolBackend()

    def test_shipped_backend_is_a_family_member(self) -> None:
        assert issubclass(JsonPartialProtocolBackend, PartialProtocolBackend)

    def test_replacement_backend_owns_its_wire_format(self) -> None:
        backend = _TurboProtocolBackend()
        assert backend.content_type == "text/vnd.turbo-stream.html"
        assert backend.serialize_envelope(_sample_envelope()) == b"2"
        assert backend.sse_event(_sample_envelope()).startswith("event: turbo\n")

    def test_replacement_backend_inherits_options(self) -> None:
        backend = _TurboProtocolBackend({"OPTIONS": {"VERSION": "7"}})
        assert backend.options == {"VERSION": "7"}
