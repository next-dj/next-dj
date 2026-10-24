"""The realtime WebSocket frame codec, the wire format any gateway has to speak."""

import enum
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from django.core.exceptions import ImproperlyConfigured

from next.partial.backends import PartialProtocolBackend
from next.partial.stream import StreamMeta, StreamPosition, StreamSkip, stream_hello

from .messages import Message, Position


MAX_INBOUND_BYTES: Final = 64 * 1024
MAX_CURSOR_TOPICS: Final = 64
SUB_TIMEOUT: Final = 5.0

_TRANSPORTS: Final = ("ws", "sse")
_SUBSCRIBE_KEYS: Final = frozenset({"type", "ticket", "last_seq"})
_POSITION_KEYS: Final = frozenset({"epoch", "seq"})
_SSE_LINE: Final = re.compile(r"\r\n|\r|\n")
_EMPTY_CURSOR: Final[Mapping[str, Position | int]] = MappingProxyType({})


class Close(enum.IntEnum):
    """Close codes of the realtime WebSocket protocol.

    The client falls back to SSE on `SESSION` and `TOPIC_DENIED` and retries the rest.
    """

    GOING_AWAY = 1001
    TOO_BIG = 1009
    INTERNAL = 1011
    TRY_AGAIN = 1013
    PROTOCOL = 4400
    SESSION = 4401
    TOPIC_DENIED = 4403
    RATE_LIMITED = 4429


class FrameError(ValueError):
    """Raised for an inbound frame that ends the connection with its close code."""

    def __init__(self, code: Close = Close.PROTOCOL) -> None:
        """Remember the close code the connection ends with."""
        self.code = code
        super().__init__(f"Malformed realtime WebSocket frame, close code {int(code)}.")


@dataclass(frozen=True, slots=True)
class Subscribe:
    """The first client frame, a ticket and the last position seen on each topic.

    A missing or non-string ticket reads as empty, so it fails as a bad ticket.
    """

    ticket: str
    cursor: Mapping[str, Position | int] = field(default=_EMPTY_CURSOR)


@dataclass(frozen=True, slots=True)
class Pong:
    """A client's answer to a server ping."""


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A JSON object frame whose type this server does not handle."""


type Inbound = Subscribe | Pong | Unsupported

_PONG: Final = Pong()
_UNSUPPORTED: Final = Unsupported()


def parse(text: str | None, data: bytes | None = None) -> Inbound:
    """Decode one inbound frame, raising `FrameError` for one that ends the socket."""
    if data is not None:
        too_big = len(data) > MAX_INBOUND_BYTES
        raise FrameError(Close.TOO_BIG if too_big else Close.PROTOCOL)
    if not text:
        raise FrameError
    if _oversized(text):
        raise FrameError(Close.TOO_BIG)
    try:
        frame = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise FrameError from exc
    if not isinstance(frame, dict):
        raise FrameError
    kind = frame.get("type")
    reader = _READERS.get(kind) if isinstance(kind, str) else None
    if reader is None:
        return _UNSUPPORTED
    return reader(frame)


def _oversized(text: str) -> bool:
    """Whether the UTF-8 form of a text frame exceeds the inbound limit."""
    if len(text) > MAX_INBOUND_BYTES:
        return True
    if len(text) * 4 <= MAX_INBOUND_BYTES:
        return False
    return len(text.encode(errors="surrogatepass")) > MAX_INBOUND_BYTES


def _subscribe(frame: dict[str, object]) -> Subscribe:
    if not frame.keys() <= _SUBSCRIBE_KEYS:
        raise FrameError
    ticket = frame.get("ticket")
    return Subscribe(
        ticket if isinstance(ticket, str) else "", _cursor(frame.get("last_seq"))
    )


def _pong(frame: dict[str, object]) -> Pong:
    if len(frame) != 1:
        raise FrameError
    return _PONG


_READERS: Final[Mapping[str, Callable[[dict[str, object]], Inbound]]] = (
    MappingProxyType({"sub": _subscribe, "pong": _pong})
)


def _cursor(value: object) -> Mapping[str, Position | int]:
    """Read `last_seq`, a bare sequence or an `{epoch, seq}` entry per topic."""
    if value is None:
        return _EMPTY_CURSOR
    if not isinstance(value, dict) or len(value) > MAX_CURSOR_TOPICS:
        raise FrameError
    return MappingProxyType(
        {topic: _position(position) for topic, position in value.items()}
    )


def _position(value: object) -> Position | int:
    if isinstance(value, dict):
        if value.keys() != _POSITION_KEYS or not isinstance(value["epoch"], str):
            raise FrameError
        return Position(value["epoch"], _seq(value["seq"]))
    return _seq(value)


def _seq(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FrameError
    return value


def hello(
    topics: Iterable[str],
    positions: Mapping[str, StreamPosition],
    *,
    heartbeat: float,
    server_time: int,
) -> str:
    """Encode the frame that opens a live subscription with its starting positions."""
    body = stream_hello(
        topics, positions, server_time=server_time, transports=_TRANSPORTS
    ).as_dict()
    return _encode({"type": "hello", **body, "heartbeat": heartbeat})


def patches(
    message: Message, backend: PartialProtocolBackend, *, server_time: int
) -> str:
    """Encode a topic message as the backend's JSON object, or its exact SSE data."""
    meta = StreamMeta(
        server_time=server_time,
        topic=message.topic,
        seq=message.seq,
        epoch=message.epoch,
    )
    data: object = backend.stream_data(message.envelope, meta)
    if data is None:
        data = _sse_data(backend.sse_stream_event(message.envelope, meta))
    if data is None:
        msg = "A partial backend must emit an SSE data field for WebSocket streams."
        raise ImproperlyConfigured(msg)
    return _encode({"type": "patches", "data": data})


def skip(message: Message) -> str:
    """Encode the acknowledgment of a message filtered out for this subscriber."""
    body = StreamSkip(message.topic, message.seq, message.epoch).as_dict()
    return _encode({"type": "skip", **body})


def ping(server_time: int) -> str:
    """Encode the heartbeat frame an idle subscription sends."""
    return _encode({"type": "ping", "server_time": server_time})


def error(code: str) -> str:
    """Encode an error frame, sent before a close or for an unsupported frame."""
    return _encode({"type": "error", "code": code})


def _encode(frame: dict[str, Any]) -> str:
    return json.dumps(frame, separators=(",", ":"))


def _sse_data(frame: str) -> str | None:
    """Join the `data:` field lines of one SSE frame the way an EventSource does."""
    values = [
        line[6:] if line.startswith("data: ") else line[5:]
        for line in _SSE_LINE.split(frame)
        if line.startswith("data:")
    ]
    return "\n".join(values) if values else None


__all__ = [
    "MAX_CURSOR_TOPICS",
    "MAX_INBOUND_BYTES",
    "SUB_TIMEOUT",
    "Close",
    "FrameError",
    "Inbound",
    "Pong",
    "Subscribe",
    "Unsupported",
    "error",
    "hello",
    "parse",
    "patches",
    "ping",
    "skip",
]
