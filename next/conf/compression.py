"""The shape of the `COMPRESSION` scope and the reads of its values with defaults."""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, TypeGuard

from next.caches import IdentityMemo

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import (
    EMPTY,
    Section,
    Shape,
    count,
    flag,
    member,
    modes,
    number,
    optional_text,
    pick,
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
    r"[a-z0-9][a-z0-9!#$&^_.+-]*/(?:\*|[a-z0-9][a-z0-9!#$&^_.+-]*)"
)
_SIZE: Final = count(0)
_MILLIS: Final = number(positive=False)


def _codec_numbers(value: object) -> TypeGuard[Mapping[str, int]]:
    return isinstance(value, Mapping) and all(
        isinstance(key, str)
        and key
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        for key, amount in value.items()
    )


def _media_types(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) and _MEDIA.fullmatch(item) is not None for item in value
    )


_FLAG: Final = Shape(flag, "True or False")
_SIZED: Final = Shape(_SIZE, "an integer from 0")
_CODEC_NUMBERS: Final = Shape(_codec_numbers, "a dict of codec tokens to integers")

SHAPE: Final = Section(
    {
        "ENABLED": _FLAG,
        "BACKENDS": Shape(texts, "a list of dotted paths"),
        "ORDER": Shape(texts, "a list of codec tokens"),
        "LEVELS": _CODEC_NUMBERS,
        "WINDOWS": _CODEC_NUMBERS,
        "MIN_SIZE": _SIZED,
        "CONTENT_TYPES": Shape(
            _media_types,
            "a list of lower-case media types such as 'text/html' or 'text/*'",
        ),
        "STREAMS": modes(StreamOwner),
        "SSE": _FLAG,
        "SSE_TOPICS": modes(TopicMode),
        "PADDING": _SIZED,
        "COALESCE_MS": Shape(_MILLIS, "a number of milliseconds from 0"),
        "OFFLOAD_BYTES": _SIZED,
        "RESPECT_NO_TRANSFORM": _FLAG,
        "EXCLUDE": Shape(optional_text, "a dotted path or None"),
    }
)
"""Every `COMPRESSION` key with what it accepts."""


@dataclass(frozen=True, slots=True)
class CompressionSettings:
    """The `COMPRESSION` scope read once, each omitted or misfit key at its default."""

    enabled: bool
    backends: tuple[str, ...]
    order: tuple[str, ...]
    levels: Mapping[str, int]
    windows: Mapping[str, int]
    min_size: int
    content_types: tuple[str, ...]
    streams: StreamOwner
    sse: bool
    sse_topics: TopicMode
    padding: int
    coalesce_ms: float
    offload_bytes: int
    respect_no_transform: bool
    exclude: str | None


def _codecs(scope: Mapping[str, object], key: str) -> Mapping[str, int]:
    """Return the codec numbers of `key` laid over its default mapping."""
    default: Mapping[str, int] = FROZEN_DEFAULTS[SCOPE][key]
    value = scope.get(key)
    if key in scope and _codec_numbers(value):
        return MappingProxyType({**default, **value})
    return default


def read_settings(scope: Mapping[str, object]) -> CompressionSettings:
    """Return the settings `scope` asks for, the default replacing each misfit."""
    defaults: Mapping[str, Any] = FROZEN_DEFAULTS[SCOPE]
    return CompressionSettings(
        enabled=pick(scope, "ENABLED", flag, defaults),
        backends=tuple(pick(scope, "BACKENDS", texts, defaults)),
        order=tuple(pick(scope, "ORDER", texts, defaults)),
        levels=_codecs(scope, "LEVELS"),
        windows=_codecs(scope, "WINDOWS"),
        min_size=pick(scope, "MIN_SIZE", _SIZE, defaults),
        content_types=tuple(pick(scope, "CONTENT_TYPES", _media_types, defaults)),
        streams=StreamOwner(pick(scope, "STREAMS", member(StreamOwner), defaults)),
        sse=pick(scope, "SSE", flag, defaults),
        sse_topics=TopicMode(pick(scope, "SSE_TOPICS", member(TopicMode), defaults)),
        padding=pick(scope, "PADDING", _SIZE, defaults),
        coalesce_ms=float(pick(scope, "COALESCE_MS", _MILLIS, defaults)),
        offload_bytes=pick(scope, "OFFLOAD_BYTES", _SIZE, defaults),
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
    "CompressionSettings",
    "StreamOwner",
    "TopicMode",
    "compression_settings",
    "read_settings",
]
