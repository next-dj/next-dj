import json
from types import MappingProxyType

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.partial import (
    Envelope,
    JsonPartialProtocolBackend,
    PartialProtocolBackend,
    StreamMeta,
)
from next.realtime import frames
from next.realtime.frames import (
    MAX_CURSOR_TOPICS,
    MAX_INBOUND_BYTES,
    SUB_TIMEOUT,
    Close,
    FrameError,
    Pong,
    Subscribe,
    Unsupported,
)
from next.realtime.messages import Message, Position
from tests.support.codecs import (
    MimeJsonCodec,
    SseEventJsonCodec,
    SseStreamEventJsonCodec,
    TextStreamCodec,
)
from tests.support.realtime import refresh_envelope


class FixedFrameCodec(TextStreamCodec):
    """A text codec whose topic frame is a fixed SSE text chosen by the test."""

    frame = ""

    def sse_stream_event(self, envelope: Envelope, meta: StreamMeta) -> str:
        del envelope, meta
        return self.frame


def _fixed(frame: str) -> FixedFrameCodec:
    codec = FixedFrameCodec()
    codec.frame = frame
    return codec


def _message(**overrides: object) -> Message:
    fields: dict[str, object] = {
        "topic": "poll:1",
        "seq": 3,
        "envelope": refresh_envelope(),
        "published_at": 1.5,
        "epoch": "e1",
    }
    fields.update(overrides)
    return Message(**fields)


def _sub(**fields: object) -> str:
    return json.dumps({"type": "sub", **fields})


def test_close_codes_match_the_wire_specification() -> None:
    assert {code.name: int(code) for code in Close} == {
        "GOING_AWAY": 1001,
        "TOO_BIG": 1009,
        "INTERNAL": 1011,
        "TRY_AGAIN": 1013,
        "PROTOCOL": 4400,
        "SESSION": 4401,
        "TOPIC_DENIED": 4403,
        "RATE_LIMITED": 4429,
    }


def test_limits_match_the_wire_specification() -> None:
    assert MAX_INBOUND_BYTES == 64 * 1024
    assert MAX_CURSOR_TOPICS == 64
    assert SUB_TIMEOUT == 5.0


@pytest.mark.parametrize(
    ("code", "expected"), [(None, Close.PROTOCOL), (Close.TOO_BIG, Close.TOO_BIG)]
)
def test_frame_error_carries_its_close_code(
    code: Close | None, expected: Close
) -> None:
    error = FrameError() if code is None else FrameError(code)
    assert isinstance(error, ValueError)
    assert error.code is expected
    assert str(int(expected)) in str(error)


def test_parse_subscribe_reads_ticket_and_cursor() -> None:
    frame = frames.parse(
        _sub(
            ticket="signed", last_seq={"poll:1": 4, "poll:2": {"epoch": "e2", "seq": 0}}
        )
    )
    assert frame == Subscribe(
        "signed", MappingProxyType({"poll:1": 4, "poll:2": Position("e2", 0)})
    )
    assert isinstance(frame.cursor, MappingProxyType)
    with pytest.raises(TypeError):
        frame.cursor["poll:3"] = 1


@pytest.mark.parametrize(
    "fields", [{"ticket": "signed"}, {"ticket": "signed", "last_seq": None}]
)
def test_parse_subscribe_without_cursor_reads_an_empty_one(
    fields: dict[str, object],
) -> None:
    frame = frames.parse(_sub(**fields))
    assert isinstance(frame, Subscribe)
    assert frame.ticket == "signed"
    assert dict(frame.cursor) == {}
    assert isinstance(frame.cursor, MappingProxyType)


@pytest.mark.parametrize("fields", [{}, {"ticket": 42}, {"ticket": None}])
def test_parse_subscribe_missing_or_non_string_ticket_reads_empty(
    fields: dict[str, object],
) -> None:
    frame = frames.parse(_sub(**fields))
    assert isinstance(frame, Subscribe)
    assert frame.ticket == ""


def test_parse_subscribe_accepts_the_cursor_topic_cap() -> None:
    cursor = {f"poll:{index}": index for index in range(MAX_CURSOR_TOPICS)}
    frame = frames.parse(_sub(ticket="t", last_seq=cursor))
    assert isinstance(frame, Subscribe)
    assert dict(frame.cursor) == cursor


@pytest.mark.parametrize(
    "last_seq",
    [
        [],
        "poll:1",
        7,
        {f"poll:{index}": 0 for index in range(MAX_CURSOR_TOPICS + 1)},
        {"poll:1": -1},
        {"poll:1": True},
        {"poll:1": 1.5},
        {"poll:1": "3"},
        {"poll:1": None},
        {"poll:1": {"epoch": "e1"}},
        {"poll:1": {"epoch": "e1", "seq": 1, "extra": 0}},
        {"poll:1": {"epoch": 1, "seq": 1}},
        {"poll:1": {"epoch": "e1", "seq": -1}},
        {"poll:1": {"epoch": "e1", "seq": False}},
    ],
)
def test_parse_subscribe_malformed_cursor_is_a_protocol_error(last_seq: object) -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(_sub(ticket="t", last_seq=last_seq))
    assert caught.value.code is Close.PROTOCOL


def test_parse_subscribe_unknown_key_is_a_protocol_error() -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(_sub(ticket="t", session="forged"))
    assert caught.value.code is Close.PROTOCOL


def test_parse_pong_reads_the_shared_marker() -> None:
    assert frames.parse('{"type":"pong"}') == Pong()
    assert frames.parse('{"type":"pong"}') is frames.parse('{"type": "pong"}')


def test_parse_pong_with_extra_keys_is_a_protocol_error() -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse('{"type":"pong","at":1}')
    assert caught.value.code is Close.PROTOCOL


@pytest.mark.parametrize(
    "text",
    ['{"type":"later","name":"vote"}', '{"type":1}', '{"type":null}', '{"ticket":"t"}'],
)
def test_parse_object_of_unknown_type_is_unsupported(text: str) -> None:
    assert isinstance(frames.parse(text), Unsupported)


@pytest.mark.parametrize(
    "text", ["", "not json", "[]", "1", '"sub"', "null", '{"type":', "[" * 50_000]
)
def test_parse_empty_garbage_or_non_object_text_is_a_protocol_error(text: str) -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(text)
    assert caught.value.code is Close.PROTOCOL


def test_parse_without_any_payload_is_a_protocol_error() -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(None, None)
    assert caught.value.code is Close.PROTOCOL


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", Close.PROTOCOL),
        (b'{"type":"pong"}', Close.PROTOCOL),
        (b"x" * MAX_INBOUND_BYTES, Close.PROTOCOL),
        (b"x" * (MAX_INBOUND_BYTES + 1), Close.TOO_BIG),
    ],
)
def test_parse_binary_frame_always_ends_the_socket(data: bytes, code: Close) -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(None, data)
    assert caught.value.code is code


def _ticket_frame(ticket: str) -> str:
    return '{"type":"sub","ticket":"' + ticket + '"}'


@pytest.mark.parametrize(
    ("char", "count"),
    [
        ("a", MAX_INBOUND_BYTES - len(_ticket_frame(""))),
        ("\U0001f600", (MAX_INBOUND_BYTES - len(_ticket_frame(""))) // 4),
        ("é", (MAX_INBOUND_BYTES - len(_ticket_frame(""))) // 2),
    ],
)
def test_parse_text_at_the_byte_limit_is_accepted(char: str, count: int) -> None:
    text = _ticket_frame(char * count)
    assert len(text.encode()) <= MAX_INBOUND_BYTES
    frame = frames.parse(text)
    assert isinstance(frame, Subscribe)
    assert frame.ticket == char * count


@pytest.mark.parametrize(
    "text",
    [
        "a" * (MAX_INBOUND_BYTES + 1),
        "\U0001f600" * (MAX_INBOUND_BYTES // 4 + 1),
        "é" * (MAX_INBOUND_BYTES // 2 + 1),
        "\ud800" * (MAX_INBOUND_BYTES // 3 + 1),
    ],
)
def test_parse_text_over_the_byte_limit_is_too_big(text: str) -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(text)
    assert caught.value.code is Close.TOO_BIG


def test_hello_frame_carries_positions_transports_and_heartbeat() -> None:
    text = frames.hello(
        ("poll:2", "poll:1"),
        {"poll:1": Position("e1", 4), "poll:2": Position("", 0)},
        heartbeat=12.5,
        server_time=1700,
    )
    assert text == (
        '{"type":"hello","protocol":2,"server_time":1700,'
        '"topics":{"poll:2":{"epoch":"","seq":0},"poll:1":{"epoch":"e1","seq":4}},'
        '"transports":["ws","sse"],"heartbeat":12.5}'
    )


def test_hello_frame_lists_only_the_subscribed_topics() -> None:
    text = frames.hello(
        ("poll:1",),
        {"poll:1": Position("e1", 1), "poll:9": Position("e9", 9)},
        heartbeat=1,
        server_time=0,
    )
    assert json.loads(text)["topics"] == {"poll:1": {"epoch": "e1", "seq": 1}}


@pytest.mark.parametrize(
    "backend", [JsonPartialProtocolBackend(), SseEventJsonCodec(), MimeJsonCodec()]
)
def test_patches_frame_matches_the_backend_stream_object_or_sse_text(
    backend: PartialProtocolBackend,
) -> None:
    message = _message()
    meta = StreamMeta(server_time=99, topic="poll:1", seq=3, epoch="e1")
    expected = backend.stream_data(message.envelope, meta)
    if expected is None:
        sse = backend.sse_stream_event(message.envelope, meta)
        expected = "\n".join(
            line.removeprefix("data:").removeprefix(" ")
            for line in sse.splitlines()
            if line.startswith("data:")
        )
    text = frames.patches(message, backend, server_time=99)
    assert json.loads(text) == {"type": "patches", "data": expected}


def test_patches_frame_of_the_json_backend_is_an_object_with_metadata() -> None:
    data = json.loads(
        frames.patches(_message(), JsonPartialProtocolBackend(), server_time=7)
    )["data"]
    assert isinstance(data, dict)
    assert data["server_time"] == 7
    assert data["topic"] == "poll:1"
    assert data["epoch"] == "e1"
    assert data["seq"] == 3


@pytest.mark.parametrize("backend", [TextStreamCodec(), SseStreamEventJsonCodec()])
def test_patches_frame_of_a_text_codec_carries_its_exact_data_text(
    backend: PartialProtocolBackend,
) -> None:
    text = frames.patches(_message(), backend, server_time=7)
    assert text == '{"type":"patches","data":"seq=3\\ne1"}'


@pytest.mark.parametrize(
    ("frame", "data"),
    [
        ("data: one\n\n", "one"),
        ("data:one\n\n", "one"),
        ("data:  two spaces\n\n", " two spaces"),
        ("event: x\r\ndata: a\r\ndata: b\r\n\r\n", "a\nb"),
        ("event: x\rdata: a\rdata:\r\r", "a\n"),
        (": note\nid: 3\ndata: kept\nretry: 5\n\n", "kept"),
    ],
)
def test_patches_frame_joins_sse_data_lines_like_an_event_source(
    frame: str, data: str
) -> None:
    text = frames.patches(_message(), _fixed(frame), server_time=0)
    assert json.loads(text) == {"type": "patches", "data": data}


@pytest.mark.parametrize("frame", ["", "event: next-patches\n\n", ": ping\n\n"])
def test_patches_frame_without_sse_data_is_a_misconfigured_backend(frame: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="SSE data field"):
        frames.patches(_message(), _fixed(frame), server_time=0)


def test_skip_frame_acknowledges_the_filtered_position() -> None:
    assert frames.skip(_message(seq=8, epoch="e2")) == (
        '{"type":"skip","topic":"poll:1","epoch":"e2","seq":8}'
    )


def test_ping_frame_carries_the_server_time() -> None:
    assert frames.ping(1234) == '{"type":"ping","server_time":1234}'


@pytest.mark.parametrize("code", ["protocol", "auth", "unsupported"])
def test_error_frame_carries_its_code(code: str) -> None:
    assert frames.error(code) == f'{{"type":"error","code":"{code}"}}'


@pytest.mark.parametrize(
    "text",
    [
        frames.ping(5),
        frames.error("auth"),
        frames.skip(_message()),
        frames.patches(_message(), JsonPartialProtocolBackend(), server_time=0),
        frames.hello(("a",), {"a": Position("e", 1)}, heartbeat=1, server_time=0),
    ],
)
def test_outbound_frames_are_server_only_types_a_server_would_not_accept(
    text: str,
) -> None:
    assert isinstance(frames.parse(text), Unsupported)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"type":"up","topic":"room:1","name":"typing","data":{"on":true},"id":"7"}',
            frames.Up("room:1", "typing", {"on": True}, "7", 74),
        ),
        ('{"type":"up","topic":"t","name":"n"}', frames.Up("t", "n", None, None, 36)),
        (
            '{"type":"up","topic":"t","name":"n","id":null}',
            frames.Up("t", "n", None, None, 46),
        ),
        (
            '{"type":"up","topic":"t","name":"n","data":"é"}',
            frames.Up("t", "n", "é", None, 48),
        ),
    ],
)
def test_parse_reads_an_upstream_event(text: str, expected: frames.Up) -> None:
    assert frames.parse(text) == expected


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "up", "topic": "t", "name": "n", "extra": 1},
        {"type": "up", "topic": 1, "name": "n"},
        {"type": "up", "topic": "t"},
        {"type": "up", "topic": "t", "name": "n", "id": 7},
        {"type": "up", "topic": "t", "name": "n", "id": ""},
        {"type": "up", "topic": "t", "name": "n", "id": "x" * 65},
        {"type": "http", "method": "POST", "path": "/"},
        {"type": "http", "id": "1", "method": "POST", "path": "/", "cookie": "x"},
        {"type": "http", "id": "1", "method": 1, "path": "/"},
        {"type": "http", "id": "1", "method": "POST", "path": None},
        {"type": "http", "id": "1", "method": "POST", "path": "/", "body": 1},
        {"type": "http", "id": "1", "method": "POST", "path": "/", "headers": []},
        {"type": "http", "id": "1", "method": "POST", "path": "/", "headers": {"a": 1}},
    ],
)
def test_parse_refuses_a_malformed_upstream_or_request(frame: dict) -> None:
    with pytest.raises(FrameError) as caught:
        frames.parse(json.dumps(frame))
    assert caught.value.code is Close.PROTOCOL


def test_parse_reads_a_request_with_defaults() -> None:
    request = frames.parse('{"type":"http","id":"1","method":"POST","path":"/a/"}')
    assert request == frames.Request("1", "POST", "/a/", MappingProxyType({}), "")
    full = frames.parse(
        '{"type":"http","id":"2","method":"POST","path":"/a/",'
        '"headers":{"accept":"x"},"body":"n=1"}'
    )
    assert isinstance(full, frames.Request)
    assert dict(full.headers) == {"accept": "x"}
    assert full.body == "n=1"


def test_ack_and_reply_encode_compact_frames() -> None:
    assert frames.ack("1") == '{"type":"ack","id":"1","ok":true}'
    assert frames.ack("1", frames.UpstreamError.TOPIC) == (
        '{"type":"ack","id":"1","ok":false,"error":"topic"}'
    )
    assert frames.reply("2", frames.Refusal.STALE) == (
        '{"type":"http","id":"2","status":0,"error":"stale","retry":true}'
    )
    assert frames.reply("3", frames.Reply(204, {"x-next-a": "b"}, "")) == (
        '{"type":"http","id":"3","status":204,"headers":{"x-next-a":"b"},"body":""}'
    )


@pytest.mark.parametrize(
    ("refusal", "retry"),
    [
        *((refusal, True) for refusal in ("disabled", "limit", "method", "path")),
        *((refusal, True) for refusal in ("headers", "body", "stale", "transport")),
        *((refusal, False) for refusal in ("http_only", "too_big", "timeout")),
        *((refusal, False) for refusal in ("encoding", "internal")),
    ],
)
def test_a_refusal_retries_only_what_never_ran(refusal: str, *, retry: bool) -> None:
    assert frames.Refusal(refusal).retry is retry
