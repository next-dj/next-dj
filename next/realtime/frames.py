"""The realtime WebSocket frame codec, the wire format any gateway has to speak."""

import enum
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from next.partial.backends import PartialProtocolBackend
from next.partial.stream import StreamMeta, StreamPosition, StreamSkip, stream_hello

from .messages import MAX_TOPICS, Message, Position


MAX_INBOUND_BYTES: Final = 64 * 1024
MAX_ID_LENGTH: Final = 64
SUB_TIMEOUT: Final = 5.0

_TRANSPORTS: Final = ("ws", "sse")
_SUBSCRIBE_KEYS: Final = frozenset({"type", "ticket", "last_seq"})
_POSITION_KEYS: Final = frozenset({"epoch", "seq"})
_UP_KEYS: Final = frozenset({"type", "topic", "name", "data", "id"})
_REQUEST_KEYS: Final = frozenset({"type", "id", "method", "path", "headers", "body"})
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
class Up:
    """A client event on a granted topic, acknowledged only when it carries an `id`.

    `size` counts the UTF-8 bytes of the frame as it arrived.
    """

    topic: str
    name: str
    data: object
    id: str | None = None
    size: int = 0


@dataclass(frozen=True, slots=True)
class Request:
    """An HTTP request the client asks the server to run on its behalf."""

    id: str
    method: str
    path: str
    headers: Mapping[str, str]
    body: str


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A JSON object frame whose type this server does not handle."""


type Inbound = Subscribe | Pong | Up | Request | Unsupported


class UpstreamError(enum.StrEnum):
    """Why an `up` frame was not delivered, the `error` of its `ack`."""

    TOPIC = "topic"
    DENIED = "denied"
    UNKNOWN = "unknown"
    LIMIT = "limit"
    TOO_BIG = "too_big"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


class Refusal(enum.StrEnum):
    """Why a tunneled request got no HTTP answer, the `error` of its `http` frame.

    `retry` tells the client the request never ran, so it may resend it over HTTP.
    """

    DISABLED = "disabled"
    LIMIT = "limit"
    METHOD = "method"
    PATH = "path"
    HEADERS = "headers"
    BODY = "body"
    STALE = "stale"
    TRANSPORT = "transport"
    HTTP_ONLY = "http_only"
    TOO_BIG = "too_big"
    TIMEOUT = "timeout"
    ENCODING = "encoding"
    INTERNAL = "internal"

    @property
    def retry(self) -> bool:
        """Whether the request never reached the application."""
        return self in _UNSENT


_UNSENT: Final = frozenset(
    {
        Refusal.DISABLED,
        Refusal.LIMIT,
        Refusal.METHOD,
        Refusal.PATH,
        Refusal.HEADERS,
        Refusal.BODY,
        Refusal.STALE,
        Refusal.TRANSPORT,
    }
)


@dataclass(frozen=True, slots=True)
class Reply:
    """The HTTP answer to a tunneled request, its body decoded as UTF-8."""

    status: int
    headers: Mapping[str, str]
    body: str


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
    if kind == "up":
        return _up(frame, _utf8_length(text))
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


def _utf8_length(text: str) -> int:
    return len(text) if text.isascii() else len(text.encode(errors="surrogatepass"))


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


def _up(frame: dict[str, object], size: int) -> Up:
    if not frame.keys() <= _UP_KEYS:
        raise FrameError
    topic, name = frame.get("topic"), frame.get("name")
    if not isinstance(topic, str) or not isinstance(name, str):
        raise FrameError
    raw_id = frame.get("id")
    frame_id = None if raw_id is None else _id(raw_id)
    return Up(topic, name, frame.get("data"), frame_id, size)


def _request(frame: dict[str, object]) -> Request:
    if not frame.keys() <= _REQUEST_KEYS:
        raise FrameError
    method, path = frame.get("method"), frame.get("path")
    headers, body = frame.get("headers", {}), frame.get("body", "")
    if (
        not isinstance(method, str)
        or not isinstance(path, str)
        or not isinstance(body, str)
        or not isinstance(headers, dict)
        or not all(isinstance(value, str) for value in headers.values())
    ):
        raise FrameError
    return Request(
        _id(frame.get("id")), method, path, MappingProxyType(dict(headers)), body
    )


def _id(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_ID_LENGTH:
        raise FrameError
    return value


_READERS: Final[Mapping[str, Callable[[dict[str, object]], Inbound]]] = (
    MappingProxyType({"sub": _subscribe, "pong": _pong, "http": _request})
)


def _cursor(value: object) -> Mapping[str, Position | int]:
    """Read `last_seq`, a bare sequence or an `{epoch, seq}` entry per topic."""
    if value is None:
        return _EMPTY_CURSOR
    if not isinstance(value, dict) or len(value) > MAX_TOPICS:
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
    limits: Mapping[str, object] | None = None,
    connection: str | None = None,
) -> str:
    """Encode the frame that opens a live subscription with its starting positions.

    `limits` sizes the client frames, `connection` lets a page drop its own echo.
    """
    body = stream_hello(
        topics, positions, server_time=server_time, transports=_TRANSPORTS
    ).as_dict()
    frame: dict[str, object] = {"type": "hello", **body, "heartbeat": heartbeat}
    if connection is not None:
        frame["connection"] = connection
    frame.update(limits or {})
    return _encode(frame)


def patches(
    message: Message, backend: PartialProtocolBackend, *, server_time: int
) -> str:
    """Encode a topic message as the stream data of the backend, object or text.

    An unnumbered message carries no `seq` or `epoch`, so the client cursor stays.
    """
    meta = StreamMeta.of(message, server_time)
    data = backend.stream_data(message.envelope, meta)
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


def ack(frame_id: str, error: UpstreamError | None = None) -> str:
    """Encode the acknowledgment of an `up` frame, failed when it names an error."""
    if error is None:
        return _encode({"type": "ack", "id": frame_id, "ok": True})
    return _encode({"type": "ack", "id": frame_id, "ok": False, "error": error.value})


def reply(frame_id: str, answer: Reply | Refusal) -> str:
    """Encode the answer to an `http` frame, an HTTP reply or a refusal."""
    if isinstance(answer, Refusal):
        return _encode(
            {
                "type": "http",
                "id": frame_id,
                "status": 0,
                "error": answer.value,
                "retry": answer.retry,
            }
        )
    return _encode(
        {
            "type": "http",
            "id": frame_id,
            "status": answer.status,
            "headers": dict(answer.headers),
            "body": answer.body,
        }
    )


def _encode(frame: dict[str, Any]) -> str:
    return json.dumps(frame, separators=(",", ":"))


__all__ = [
    "MAX_ID_LENGTH",
    "MAX_INBOUND_BYTES",
    "MAX_TOPICS",
    "SUB_TIMEOUT",
    "Close",
    "FrameError",
    "Inbound",
    "Pong",
    "Refusal",
    "Reply",
    "Request",
    "Subscribe",
    "Unsupported",
    "Up",
    "UpstreamError",
    "ack",
    "error",
    "hello",
    "parse",
    "patches",
    "ping",
    "reply",
    "skip",
]
