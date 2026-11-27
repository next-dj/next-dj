"""The frames of streamed partial responses and the block a partial render opens."""

from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Protocol, Self, runtime_checkable

from . import keys
from .headers import accepts_event_stream


if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from django.http import HttpRequest, HttpResponseBase

    from .envelope import Envelope


PROTOCOL: Final = 2
"""The version of the stream wire each hello frame names, realtime and partial alike."""

HELLO_EVENT: Final = "hello"
"""The event name of the first frame of a streamed partial response."""

PATCH_EVENT: Final = "patch"
"""The event name of the envelope frame and of each settled boundary frame."""

END_EVENT: Final = "end"
"""The event name of the last frame of a streamed partial response."""

_GET: Final = "GET"


class StreamPosition(Protocol):
    """The `(epoch, seq)` position of one topic, read from any broker's own type."""

    @property
    def epoch(self) -> str:
        """Counter epoch that numbered the topic."""

    @property
    def seq(self) -> int:
        """Latest sequence within the epoch."""


class TopicPosition(StreamPosition, Protocol):
    """The position of one message together with the topic it was published on."""

    @property
    def topic(self) -> str:
        """Topic the message was published on."""


@dataclass(frozen=True, slots=True)
class StreamMeta:
    """Per-frame metadata kept separate from the ordinary HTTP envelope."""

    server_time: int
    topic: str | None = None
    seq: int | None = None
    epoch: str | None = None

    @classmethod
    def of(cls, message: TopicPosition, server_time: int) -> Self:
        """Return a topic message's metadata, an unnumbered one without a position."""
        if not message.seq:
            return cls(server_time=server_time, topic=message.topic)
        return cls(
            server_time=server_time,
            topic=message.topic,
            seq=message.seq,
            epoch=message.epoch,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return only metadata present on this stream frame."""
        data: dict[str, Any] = {keys.SERVER_TIME: self.server_time}
        if self.topic is not None:
            data[keys.TOPIC] = self.topic
        if self.epoch is not None:
            data[keys.EPOCH] = self.epoch
        if self.seq is not None:
            data[keys.SEQ] = self.seq
        return data


@dataclass(frozen=True, slots=True)
class StreamHello:
    """Initial `(epoch, seq)` position per topic, announced once a subscription is live.

    The topics are frozen into a read-only mapping so a hook cannot alter the snapshot.
    """

    server_time: int
    topics: Mapping[str, tuple[str, int]]
    transports: tuple[str, ...] = ("sse",)
    protocol: int = PROTOCOL
    streamed: bool = False

    def __post_init__(self) -> None:
        """Freeze the topic positions behind a read-only mapping."""
        frozen = MappingProxyType(dict(self.topics))
        object.__setattr__(self, "topics", frozen)

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh copy of the hello control frame's JSON body.

        A streamed partial response names only the protocol, as it follows no topic.
        """
        if self.streamed:
            return {"protocol": self.protocol, "streamed": True}
        return {
            "protocol": self.protocol,
            keys.SERVER_TIME: self.server_time,
            "topics": {
                topic: {keys.EPOCH: epoch, keys.SEQ: seq}
                for topic, (epoch, seq) in self.topics.items()
            },
            "transports": list(self.transports),
        }


def stream_hello(
    topics: Iterable[str],
    positions: Mapping[str, StreamPosition],
    *,
    server_time: int,
    transports: tuple[str, ...],
) -> StreamHello:
    """Build the hello frame both stream transports send once a subscription is live."""
    return StreamHello(
        server_time=server_time,
        topics={
            topic: (positions[topic].epoch, positions[topic].seq) for topic in topics
        },
        transports=transports,
    )


@dataclass(frozen=True, slots=True)
class StreamSkip:
    """Sequence acknowledgment for a message filtered for this subscriber."""

    topic: str
    seq: int
    epoch: str

    def as_dict(self) -> dict[str, Any]:
        """Return the skip control frame's JSON body."""
        return {keys.TOPIC: self.topic, keys.EPOCH: self.epoch, keys.SEQ: self.seq}


@dataclass(frozen=True, slots=True)
class StreamEnd:
    """Terminal frame telling the client not to reconnect a closed stream."""

    code: str

    def as_dict(self) -> dict[str, Any]:
        """Return the end control frame's JSON body."""
        return {"code": self.code}


@dataclass(frozen=True, slots=True)
class StreamPatch:
    """One `patch` frame of a streamed partial response, an envelope and its wrapper."""

    data: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return the patch frame's JSON body."""
        return dict(self.data)


class Deferred(Protocol):
    """The deferred boundaries a partial render left to stream after its envelope."""

    def respond(
        self,
        envelope: "Envelope",
        *,
        page_path: "Path | None" = None,
        compress: bool = True,
    ) -> "HttpResponseBase":
        """Return the stream whose first frame is `envelope`, boundaries after it."""

    def release(self) -> None:
        """Give back the stream slot no stream response took."""


@runtime_checkable
class Discardable(Protocol):
    """A streamed response that holds its slot until it is served or discarded."""

    def discard(self) -> None:
        """Give back what the response holds, as its view never returned it."""


class Streamable:
    """The partial render a stream may follow, holding what its boundaries left.

    The deferral sink fills `deferred` when the policy lets the response stream.
    """

    __slots__ = ("asked", "deferred")

    def __init__(self) -> None:
        """Hold nothing until a render inside the block queues a deferred job."""
        self.asked = False
        self.deferred: Deferred | None = None


_streamable: ContextVar[Streamable | None] = ContextVar("next_streamable", default=None)


class _Streaming:
    """The block of one streamable partial render, bound while it is entered."""

    __slots__ = ("_token",)

    def __enter__(self) -> Streamable:
        """Bind a fresh `Streamable` as the one the render inside streams from."""
        held = Streamable()
        self._token = _streamable.set(held)
        return held

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: "TracebackType | None",
    ) -> None:
        """Unbind the block, whatever the render inside it did."""
        _streamable.reset(self._token)


_CLOSED: Final[AbstractContextManager[Streamable | None]] = nullcontext()


def streamable(request: "HttpRequest") -> AbstractContextManager[Streamable | None]:
    """Let the deferred boundaries the block renders stream after the first frame.

    A request other than a `GET` accepting `text/event-stream` opens no block.
    """
    if request.method != _GET or not accepts_event_stream(request):
        return _CLOSED
    return _Streaming()


def current_streamable() -> Streamable | None:
    """Return the block a partial render streams from, `None` outside one."""
    return _streamable.get()


class _Owned:
    """The block that discards a streamed response a failure inside it leaves."""

    __slots__ = ("_response",)

    def __init__(self, response: "HttpResponseBase") -> None:
        """Hold the response the block answers with."""
        self._response = response

    def __enter__(self) -> None:
        """Open the block, holding nothing more."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: "TracebackType | None",
    ) -> None:
        """Discard a streamed response the block failed, letting the failure pass."""
        if exc_type is not None and isinstance(self._response, Discardable):
            self._response.discard()


def owned(response: "HttpResponseBase") -> AbstractContextManager[None]:
    """Discard a streamed `response` the block fails, so its slot never waits on gc.

    It never closes the response, whose `close` fires `request_finished` mid-request.
    """
    return _Owned(response)


__all__ = [
    "END_EVENT",
    "HELLO_EVENT",
    "PATCH_EVENT",
    "PROTOCOL",
    "Deferred",
    "Discardable",
    "StreamEnd",
    "StreamHello",
    "StreamMeta",
    "StreamPatch",
    "StreamPosition",
    "StreamSkip",
    "Streamable",
    "TopicPosition",
    "current_streamable",
    "owned",
    "stream_hello",
    "streamable",
]
