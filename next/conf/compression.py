"""The shape of the `COMPRESSION` scope and the reads of its values with defaults."""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeGuard

from next.caches import IdentityMemo
from next.errors import DurationError
from next.utils import parse_duration

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import (
    EMPTY,
    Section,
    Shape,
    count,
    enforce,
    flag,
    member,
    modes,
    optional_text,
    pick,
    text,
    texts,
)


SCOPE: Final = "COMPRESSION"
OPT_OUT: Final = "next_compress"
"""The response attribute that, set to `False`, keeps that response uncompressed.

A page sets it and the compressors read it, so neither area imports the other.
"""


class StreamOwner(enum.StrEnum):
    """Who compresses a next.dj document stream, the stream itself or the middleware."""

    SELF = "self"
    MIDDLEWARE = "middleware"


class TopicMode(enum.StrEnum):
    """How a realtime topic stream shares the compression window across its events."""

    OFF = "off"
    RESET = "reset"
    SHARED = "shared"


_MEDIA: Final = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]*/"
    r"(?:\*(?:\+[a-z0-9][a-z0-9!#$&^_.-]*)?|[a-z0-9][a-z0-9!#$&^_.+-]*)"
)
_SIZE: Final = count(0)


def _seconds(value: object) -> bool:
    try:
        parse_duration(value)
    except DurationError:
        return False
    return True


_ENTRY_KEYS: Final = frozenset({"BACKEND", "OPTIONS"})
_OPTION_KEYS: Final = frozenset({"LEVEL", "WINDOW"})


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _options(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.keys() <= _OPTION_KEYS
        and all(map(_integer, value.values()))
    )


def _entry(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.keys() <= _ENTRY_KEYS
        and text(value.get("BACKEND"))
        and _options(value.get("OPTIONS", EMPTY))
    )


def _entries(value: object) -> TypeGuard[Sequence[Mapping[str, Any]]]:
    return isinstance(value, list | tuple) and all(map(_entry, value))


def _media_types(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) and _MEDIA.fullmatch(item) is not None for item in value
    )


_FLAG: Final = Shape(flag, "True or False")
_SIZED: Final = Shape(_SIZE, "an integer from 0")

SHAPE: Final = Section(
    {
        "ENABLED": _FLAG,
        "BACKENDS": Shape(
            _entries,
            "a list of dicts, each with a dotted path in 'BACKEND' and optional "
            "'OPTIONS' holding an integer 'LEVEL' and 'WINDOW'",
        ),
        "ORDER": Shape(texts, "a list of codec tokens"),
        "MIN_SIZE": _SIZED,
        "CONTENT_TYPES": Shape(
            _media_types,
            "a list of lower-case media types such as 'text/html', 'text/*' or "
            "'application/*+json'",
        ),
        "STREAMS": modes(StreamOwner),
        "SSE": _FLAG,
        "SSE_TOPICS": modes(TopicMode),
        "PADDING": _SIZED,
        "COALESCE": Shape(_seconds, "a duration from 0, in seconds or as '2s'"),
        "RESPECT_NO_TRANSFORM": _FLAG,
        "EXCLUDE": Shape(optional_text, "a dotted path or None"),
    }
)
"""Every `COMPRESSION` key with what it accepts."""


@dataclass(frozen=True, slots=True)
class CodecEntry:
    """One `BACKENDS` entry as written, its class path and the options it sets."""

    path: str
    level: int | None
    window: int | None
    config: Mapping[str, Any]


def _codec_entry(config: Mapping[str, Any]) -> CodecEntry:
    options: Mapping[str, int] = config.get("OPTIONS", EMPTY)
    return CodecEntry(
        config["BACKEND"], options.get("LEVEL"), options.get("WINDOW"), config
    )


@dataclass(frozen=True, slots=True)
class CompressionSettings:
    """The `COMPRESSION` scope read once, each omitted key at its default."""

    enabled: bool
    backends: tuple[CodecEntry, ...]
    order: tuple[str, ...]
    min_size: int
    content_types: tuple[str, ...]
    streams: StreamOwner
    sse: bool
    sse_topics: TopicMode
    padding: int
    coalesce: float
    respect_no_transform: bool
    exclude: str | None


def read_settings(scope: Mapping[str, object]) -> CompressionSettings:
    """Return the settings `scope` asks for, refusing a value outside its shape."""
    defaults: Mapping[str, Any] = FROZEN_DEFAULTS[SCOPE]
    enforce(f"NEXT_FRAMEWORK[{SCOPE!r}]", scope, SHAPE)
    return CompressionSettings(
        enabled=pick(scope, "ENABLED", flag, defaults),
        backends=tuple(map(_codec_entry, pick(scope, "BACKENDS", _entries, defaults))),
        order=tuple(pick(scope, "ORDER", texts, defaults)),
        min_size=pick(scope, "MIN_SIZE", _SIZE, defaults),
        content_types=tuple(pick(scope, "CONTENT_TYPES", _media_types, defaults)),
        streams=StreamOwner(pick(scope, "STREAMS", member(StreamOwner), defaults)),
        sse=pick(scope, "SSE", flag, defaults),
        sse_topics=TopicMode(pick(scope, "SSE_TOPICS", member(TopicMode), defaults)),
        padding=pick(scope, "PADDING", _SIZE, defaults),
        coalesce=parse_duration(scope.get("COALESCE", defaults["COALESCE"])),
        respect_no_transform=pick(scope, "RESPECT_NO_TRANSFORM", flag, defaults),
        exclude=pick(scope, "EXCLUDE", optional_text, defaults),
    )


def _read() -> CompressionSettings:
    """Read the settings of the merged `COMPRESSION` mapping, empty for no mapping."""
    held = next_framework_settings.COMPRESSION
    return read_settings(held if isinstance(held, Mapping) else EMPTY)


_SETTINGS: Final[IdentityMemo[CompressionSettings]] = IdentityMemo()


def compression_settings() -> CompressionSettings:
    """Return the `COMPRESSION` settings, read again once the merged mapping moves."""
    return _SETTINGS.get(next_framework_settings.COMPRESSION, _read)


__all__ = [
    "OPT_OUT",
    "SCOPE",
    "SHAPE",
    "CodecEntry",
    "CompressionSettings",
    "StreamOwner",
    "TopicMode",
    "compression_settings",
    "read_settings",
]
