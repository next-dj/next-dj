import json

import pytest

from next.partial import (
    Envelope,
    JsonPartialProtocolBackend,
    PartialProtocolBackend,
    Patches,
)
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.partial.signals import partial_backend_loaded
from next.partial.stream import StreamEnd, StreamHello, StreamMeta, StreamSkip
from next.testing import capture_signals


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


class TestDeserializeEnvelope:
    """The default backend reads back the envelope it wrote."""

    def test_body_round_trips_through_the_backend(self) -> None:
        backend = JsonPartialProtocolBackend()
        envelope = _sample_envelope()
        assert backend.deserialize_envelope(backend.serialize_envelope(envelope)) == (
            envelope
        )

    def test_reader_carries_the_ops_of_the_body(self) -> None:
        backend = JsonPartialProtocolBackend()
        body = backend.serialize_envelope(_sample_envelope())
        read = backend.deserialize_envelope(body)
        assert [op.op for op in read.ops] == ["replace", "event"]


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

    def test_stream_metadata_is_added_only_to_the_sse_copy(self) -> None:
        backend = JsonPartialProtocolBackend()
        envelope = _sample_envelope()
        before = backend.serialize_envelope(envelope)
        frame = backend.sse_stream_event(
            envelope, StreamMeta(server_time=1700, topic="room:7", seq=3, epoch="e1")
        )
        data = json.loads(frame.splitlines()[1][len("data: ") :])
        assert data["server_time"] == 1700
        assert data["topic"] == "room:7"
        assert data["epoch"] == "e1"
        assert data["seq"] == 3
        assert backend.serialize_envelope(envelope) == before
        assert "server_time" not in json.loads(before)

    def test_stream_control_frames_have_protocol_event_names(self) -> None:
        backend = JsonPartialProtocolBackend()
        assert backend.sse_hello({"protocol": 2}).splitlines() == [
            "event: next-hello",
            'data: {"protocol":2}',
            "",
        ]
        skip = StreamSkip("room:7", 4, "e1")
        assert backend.sse_skip(skip.as_dict()).splitlines() == [
            "event: next-skip",
            'data: {"topic":"room:7","epoch":"e1","seq":4}',
            "",
        ]

    def test_end_frame_has_its_protocol_event_name(self) -> None:
        backend = JsonPartialProtocolBackend()
        assert backend.sse_end(StreamEnd("auth").as_dict()) == (
            'event: next-end\ndata: {"code":"auth"}\n\n'
        )

    def test_stream_event_matches_the_body_plus_metadata(self) -> None:
        backend = JsonPartialProtocolBackend()
        envelope = _sample_envelope()
        frame = backend.sse_stream_event(envelope, StreamMeta(server_time=5))
        data = json.loads(frame.splitlines()[1][len("data: ") :])
        assert data == {
            **json.loads(backend.serialize_envelope(envelope)),
            "server_time": 5,
        }


class _WrappedJsonBackend(JsonPartialProtocolBackend):
    def sse_event(self, envelope: Envelope) -> str:
        wrapped = json.dumps({"wrapped": envelope.as_dict()})
        return f"event: next-patches\ndata: {wrapped}\n\n"


class _HttpOnlyJsonBackend(JsonPartialProtocolBackend):
    def serialize_envelope(self, envelope: Envelope) -> bytes:
        return json.dumps({"http": envelope.version}).encode()


class TestJsonSubclassStreamEvents:
    """A JSON subclass keeps its own SSE shape, and HTTP overrides leave SSE alone."""

    def test_overridden_sse_event_wins_over_the_metadata_path(self) -> None:
        backend = _WrappedJsonBackend()
        envelope = _sample_envelope()
        frame = backend.sse_stream_event(envelope, StreamMeta(server_time=1, seq=2))
        assert frame == backend.sse_event(envelope)
        assert backend.serialize_envelope(envelope) != frame.encode()

    def test_http_only_override_keeps_metadata_on_the_stream(self) -> None:
        backend = _HttpOnlyJsonBackend()
        envelope = _sample_envelope()
        frame = backend.sse_stream_event(envelope, StreamMeta(server_time=1, seq=2))
        data = json.loads(frame.splitlines()[1][len("data: ") :])
        assert data["seq"] == 2
        assert data["ops"] == envelope.as_dict()["ops"]
        assert "http" not in data


class TestStreamFrames:
    """Stream control frames serialize the shared wire schema."""

    def test_meta_omits_absent_fields(self) -> None:
        assert StreamMeta(server_time=3).as_dict() == {"server_time": 3}

    def test_hello_holds_an_immutable_snapshot_and_returns_copies(self) -> None:
        topics = {"room:1": ("e1", 4)}
        hello = StreamHello(server_time=9, topics=topics)
        topics["room:2"] = ("e2", 1)
        assert dict(hello.topics) == {"room:1": ("e1", 4)}
        with pytest.raises(TypeError):
            hello.topics["room:3"] = ("e3", 0)
        frame = hello.as_dict()
        assert frame == {
            "protocol": 2,
            "server_time": 9,
            "topics": {"room:1": {"epoch": "e1", "seq": 4}},
            "transports": ["sse"],
        }
        frame["topics"]["room:1"]["seq"] = 99
        assert hello.as_dict()["topics"]["room:1"]["seq"] == 4


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

    def deserialize_envelope(self, body: bytes) -> Envelope:
        return Envelope(version=body.decode())


class _OpaqueProtocolBackend(_TurboProtocolBackend):
    def serialize_envelope(self, envelope: Envelope) -> bytes:
        return b"turbo-stream"


class _CustomSseProtocolBackend(_TurboProtocolBackend):
    def serialize_envelope(self, envelope: Envelope) -> bytes:
        return b'{"version":"custom"}'

    def sse_event(self, envelope: Envelope) -> str:
        return 'event: custom\ndata: {"wrapped":true}\n\n'


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

    def test_replacement_backend_reads_its_own_format_back(self) -> None:
        backend = _TurboProtocolBackend()
        assert backend.deserialize_envelope(b"v9").version == "v9"

    def test_custom_sse_framing_is_not_replaced_by_http_json(self) -> None:
        backend = _CustomSseProtocolBackend()
        assert backend.sse_stream_event(
            _sample_envelope(), StreamMeta(server_time=1)
        ) == backend.sse_event(_sample_envelope())

    def test_non_object_json_backend_keeps_its_legacy_stream_event(self) -> None:
        backend = _TurboProtocolBackend()
        assert backend.sse_stream_event(
            _sample_envelope(), StreamMeta(server_time=1)
        ) == backend.sse_event(_sample_envelope())

    def test_unparsable_backend_keeps_its_legacy_stream_event(self) -> None:
        backend = _OpaqueProtocolBackend()
        assert backend.sse_stream_event(
            _sample_envelope(), StreamMeta(server_time=1)
        ) == backend.sse_event(_sample_envelope())

    def test_a_backend_without_a_reader_is_abstract(self) -> None:
        class _WriteOnly(PartialProtocolBackend):
            content_type = "text/plain"

            def serialize_envelope(self, envelope: Envelope) -> bytes:
                return b""

            def sse_event(self, envelope: Envelope) -> str:
                return ""

        with pytest.raises(TypeError, match="deserialize_envelope"):
            _WriteOnly()


@pytest.fixture()
def uncached_backend_manager():
    """Drop the cached protocol backend around a test, the manager is global."""
    partial_backend_manager.reset()
    yield partial_backend_manager
    partial_backend_manager.reset()


@pytest.mark.usefixtures("uncached_backend_manager")
class TestBackendLoadedSignal:
    """Building the protocol backend announces the class the settings selected."""

    def test_the_build_announces_the_class_and_the_instance(self) -> None:
        with capture_signals(partial_backend_loaded) as recorded:
            backend = partial_backend_manager.get()
        event = recorded.first_for(partial_backend_loaded)
        assert event.sender is type(backend)
        assert event.kwargs["instance"] is backend

    def test_the_announcement_carries_the_settings_entry(self) -> None:
        with capture_signals(partial_backend_loaded) as recorded:
            partial_backend_manager.get()
        event = recorded.first_for(partial_backend_loaded)
        assert isinstance(event.kwargs["config"], dict)

    def test_a_cached_backend_is_not_announced_again(self) -> None:
        # the signal marks a build, so it must not fire once per partial response
        partial_backend_manager.get()
        with capture_signals(partial_backend_loaded) as recorded:
            partial_backend_manager.get()
        assert len(recorded) == 0
