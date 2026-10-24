"""Immutable broker messages, topic positions and the topic names that address them."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from next.partial.envelope import Envelope

from .errors import InvalidTopicError


_TOPIC = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_RESERVED_PREFIX = "next:"


def validate_topic(topic: str) -> str:
    """Return a valid topic name or raise with the rejected value."""
    if not isinstance(topic, str) or _TOPIC.fullmatch(topic) is None:
        raise InvalidTopicError(topic)
    if topic.startswith(_RESERVED_PREFIX):
        raise InvalidTopicError(topic)
    return topic


@dataclass(frozen=True, slots=True)
class Position:
    """The latest sequence of a topic within the counter epoch that numbered it."""

    epoch: str
    seq: int


@dataclass(frozen=True, slots=True)
class Message:
    """One envelope published on a topic, numbered by the broker within an epoch."""

    topic: str
    seq: int
    envelope: Envelope
    published_at: float
    epoch: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the message as a JSON-safe mapping."""
        return {
            "topic": self.topic,
            "seq": self.seq,
            "epoch": self.epoch,
            "envelope": self.envelope.as_dict(),
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        """Rebuild a message from its JSON-safe mapping, rejecting wrong wire types."""
        topic = data.get("topic")
        seq = data.get("seq")
        epoch = data.get("epoch", "")
        envelope = data.get("envelope")
        published_at = data.get("published_at")
        if not isinstance(topic, str):
            msg = "A broker message needs a string topic."
            raise TypeError(msg)
        if not isinstance(seq, int) or isinstance(seq, bool):
            msg = "A broker message needs an integer seq."
            raise TypeError(msg)
        if seq < 1:
            msg = "A broker message seq must be positive."
            raise ValueError(msg)
        if not isinstance(epoch, str):
            msg = "A broker message epoch must be a string."
            raise TypeError(msg)
        if not isinstance(published_at, int | float) or isinstance(published_at, bool):
            msg = "A broker message needs a numeric published_at."
            raise TypeError(msg)
        if not isinstance(envelope, Mapping):
            msg = "A broker message needs an envelope mapping."
            raise TypeError(msg)
        try:
            decoded = Envelope.from_dict(envelope)
        except (KeyError, TypeError, AttributeError) as exc:
            msg = "A broker message carries a malformed envelope."
            raise TypeError(msg) from exc
        return cls(topic, seq, decoded, float(published_at), epoch)


__all__ = ["Message", "Position", "validate_topic"]
