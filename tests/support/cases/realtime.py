import json
from collections.abc import Mapping
from dataclasses import dataclass

from next.partial import PartialProtocolBackend
from next.realtime.frames import Inbound, Pong, Subscribe, Up
from tests.support.codecs import (
    InstanceMimeJsonCodec,
    MimeJsonCodec,
    TextDataJsonCodec,
    TextStreamCodec,
)
from tests.support.realtime import ROOM_AUTHORIZER_REF


@dataclass(frozen=True, slots=True)
class RefusedFrameCase:
    """One ``websocket.receive`` payload the consumer refuses, its error and close code."""

    id: str
    receive: Mapping[str, object]
    error: str = "protocol"
    close_code: int = 4400


def _json_frame(case_id: str, frame: object) -> RefusedFrameCase:
    return RefusedFrameCase(case_id, {"text": json.dumps(frame)})


MALFORMED_SUBSCRIPTION_CASES: tuple[RefusedFrameCase, ...] = (
    _json_frame("not_an_object", []),
    _json_frame("unknown_key", {"type": "sub", "ticket": "x", "unknown": True}),
    _json_frame("unknown_type", {"type": "other"}),
    _json_frame("negative_cursor", {"type": "sub", "ticket": "x", "last_seq": -1}),
    _json_frame(
        "cursor_without_epoch",
        {"type": "sub", "ticket": "x", "last_seq": {"room:1": {"seq": 1}}},
    ),
    _json_frame(
        "cursor_over_topic_limit",
        {"type": "sub", "ticket": "x", "last_seq": {f"room:{i}": i for i in range(65)}},
    ),
)


RAW_FRAME_CASES: tuple[RefusedFrameCase, ...] = (
    RefusedFrameCase("truncated", {"text": "{"}),
    RefusedFrameCase("empty", {"text": ""}),
    RefusedFrameCase("binary", {"bytes": b'{"type":"sub"}'}),
    RefusedFrameCase("deeply_nested", {"text": "[" * 100_000}),
)


@dataclass(frozen=True, slots=True)
class TicketReferenceCase:
    """One ticket whose transform or authorizer reference the consumer cannot use."""

    id: str
    transform_ref: str | None
    authorizer_ref: str = ROOM_AUTHORIZER_REF
    error: str = "auth"
    close_code: int = 4401


UNUSABLE_REFERENCE_CASES: tuple[TicketReferenceCase, ...] = (
    TicketReferenceCase("missing_transform", "missing.module.callback"),
    TicketReferenceCase("non_callable_transform", "django.conf.settings"),
    TicketReferenceCase("missing_authorizer", None, "missing.module.callback"),
    TicketReferenceCase("non_callable_authorizer", None, "django.conf.settings"),
)


@dataclass(frozen=True, slots=True)
class TextFrameCase:
    """One codec whose stream data reaches the socket as text."""

    id: str
    backend: PartialProtocolBackend


TEXT_FRAME_CASES: tuple[TextFrameCase, ...] = (
    TextFrameCase("text_codec", TextStreamCodec()),
    TextFrameCase("stream_data_override", TextDataJsonCodec()),
    TextFrameCase("custom_mime_type", MimeJsonCodec()),
    TextFrameCase("instance_mime_type", InstanceMimeJsonCodec()),
)


@dataclass(frozen=True, slots=True)
class InboundFrameCase:
    """One client text frame the codec accepts and the frame type it decodes to."""

    id: str
    text: str
    kind: type[Inbound]


INBOUND_FRAME_CASES: tuple[InboundFrameCase, ...] = (
    InboundFrameCase("pong", json.dumps({"type": "pong"}), Pong),
    InboundFrameCase(
        "subscribe",
        json.dumps(
            {
                "type": "sub",
                "ticket": "t" * 200,
                "last_seq": {"room:1": {"epoch": "0123456789abcdef", "seq": 7}},
            }
        ),
        Subscribe,
    ),
    InboundFrameCase(
        "up",
        json.dumps(
            {"type": "up", "topic": "room:1", "name": "vote", "data": {"choice": 2}}
        ),
        Up,
    ),
)
