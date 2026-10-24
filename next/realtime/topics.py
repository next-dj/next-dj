"""Validation for public realtime topic names."""

import re

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


__all__ = ["validate_topic"]
