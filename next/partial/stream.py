"""Metadata and control frames used only by streamed partial responses."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from . import keys


@dataclass(frozen=True, slots=True)
class StreamMeta:
    """Per-frame metadata kept separate from the ordinary HTTP envelope."""

    server_time: int
    topic: str | None = None
    seq: int | None = None
    epoch: str | None = None

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
    protocol: int = 2

    def __post_init__(self) -> None:
        """Freeze the topic positions behind a read-only mapping."""
        frozen = MappingProxyType(dict(self.topics))
        object.__setattr__(self, "topics", frozen)

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh copy of the hello control frame's JSON body."""
        return {
            "protocol": self.protocol,
            keys.SERVER_TIME: self.server_time,
            "topics": {
                topic: {keys.EPOCH: epoch, keys.SEQ: seq}
                for topic, (epoch, seq) in self.topics.items()
            },
            "transports": list(self.transports),
        }


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


__all__ = ["StreamEnd", "StreamHello", "StreamMeta", "StreamSkip"]
