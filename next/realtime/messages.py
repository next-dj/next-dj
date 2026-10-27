"""Immutable broker messages, topic positions and the topic names that address them."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from next.partial.envelope import Envelope

from .errors import InvalidTopicError


_TOPIC = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_RESERVED_PREFIX = "next:"
MAX_TOPICS: Final = 64
"""The most topics one ticket grants and one subscribe frame carries a cursor for."""


def validate_topic(topic: str) -> str:
    """Return a valid topic name or raise with the rejected value."""
    if not isinstance(topic, str) or _TOPIC.fullmatch(topic) is None:
        raise InvalidTopicError(topic)
    if topic.startswith(_RESERVED_PREFIX):
        raise InvalidTopicError(topic)
    return topic


def validate_topics(
    topics: Iterable[str], *, at_least_one: bool = False
) -> tuple[str, ...]:
    """Return the valid topics once each in first-seen order, raising for a bad one."""
    selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
    if at_least_one and not selected:
        msg = "A realtime subscription needs at least one topic."
        raise ValueError(msg)
    return selected


@dataclass(frozen=True, slots=True)
class Position:
    """The latest sequence of a topic within the counter epoch that numbered it."""

    epoch: str
    seq: int

    @property
    def cursor(self) -> str:
        """Return the `<epoch>:<seq>` form an SSE id and `manage.py realtime` use."""
        return f"{self.epoch}:{self.seq}"

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-safe `{epoch, seq}` mapping of the position."""
        return {"epoch": self.epoch, "seq": self.seq}


@dataclass(frozen=True, slots=True)
class Message:
    """One envelope published on a topic, numbered by the broker within an epoch.

    A `seq` of 0 marks an unnumbered message that skips the log and moves no cursor.
    """

    topic: str
    seq: int
    envelope: Envelope
    published_at: float
    epoch: str = ""

    @property
    def numbered(self) -> bool:
        """Whether the broker numbered the message, False for an unnumbered one."""
        return self.seq > 0

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-safe mapping, without a position when unnumbered."""
        data: dict[str, Any] = {"topic": self.topic}
        if self.numbered:
            data["seq"] = self.seq
            data["epoch"] = self.epoch
        data["envelope"] = self.envelope.as_dict()
        data["published_at"] = self.published_at
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        """Rebuild a message from its JSON-safe mapping, rejecting wrong wire types.

        A mapping without `seq` is an unnumbered message.
        """
        topic = data.get("topic")
        seq = data.get("seq", 0)
        epoch = data.get("epoch", "")
        envelope = data.get("envelope")
        published_at = data.get("published_at")
        if not isinstance(topic, str):
            msg = "A broker message needs a string topic."
            raise TypeError(msg)
        if not isinstance(seq, int) or isinstance(seq, bool):
            msg = "A broker message needs an integer seq."
            raise TypeError(msg)
        if "seq" in data and seq < 1:
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


__all__ = ["MAX_TOPICS", "Message", "Position", "validate_topic", "validate_topics"]
