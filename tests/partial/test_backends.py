import json

from next.partial import Envelope, PartialProtocolBackend, Patches
from next.partial.headers import CONTENT_TYPE


def _sample_envelope() -> Envelope:
    return (
        Patches("9f3c2e1b")
        .replace({"zone": "list"}, "<div></div>")
        .event("saved", {"id": 7})
        .envelope()
    )


class TestSerializeEnvelope:
    """The default backend serialises envelopes as compact JSON bytes."""

    def test_content_type_is_vendor_mime(self) -> None:
        assert PartialProtocolBackend().content_type == CONTENT_TYPE

    def test_serialize_returns_bytes(self) -> None:
        body = PartialProtocolBackend().serialize_envelope(_sample_envelope())
        assert isinstance(body, bytes)

    def test_serialize_is_compact(self) -> None:
        body = PartialProtocolBackend().serialize_envelope(_sample_envelope())
        assert b", " not in body
        assert b": " not in body

    def test_serialize_round_trips(self) -> None:
        body = PartialProtocolBackend().serialize_envelope(_sample_envelope())
        data = json.loads(body)
        assert data["version"] == "9f3c2e1b"
        assert data["ops"][0]["op"] == "replace"
        assert data["ops"][1]["op"] == "event"

    def test_serialize_keeps_non_ascii(self) -> None:
        envelope = Patches("v1").event("сохранено").envelope()
        body = PartialProtocolBackend().serialize_envelope(envelope)
        assert "сохранено".encode() in body


class TestSseEvent:
    """The SSE frame wraps the same JSON envelope as a `next-patches` event."""

    def test_event_name_and_data(self) -> None:
        frame = PartialProtocolBackend().sse_event(_sample_envelope())
        assert frame.startswith("event: next-patches\n")
        assert frame.endswith("\n\n")

    def test_event_carries_same_json_as_body(self) -> None:
        backend = PartialProtocolBackend()
        envelope = _sample_envelope()
        body = backend.serialize_envelope(envelope).decode()
        frame = backend.sse_event(envelope)
        data_line = frame.splitlines()[1]
        assert data_line == f"data: {body}"


class TestBackendOptions:
    """OPTIONS from the settings entry are exposed on the backend."""

    def test_options_read_from_config(self) -> None:
        backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
        assert backend.options["VERSION"] == "manifest"

    def test_options_default_empty(self) -> None:
        assert PartialProtocolBackend().options == {}

    def test_non_dict_options_falls_back_to_empty(self) -> None:
        backend = PartialProtocolBackend({"OPTIONS": "bogus"})
        assert backend.options == {}
