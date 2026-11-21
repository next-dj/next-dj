"""The shape of the `COMPRESSION` scope and the reads of its values with defaults."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, TypeGuard

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings


SCOPE: Final = "COMPRESSION"
OPT_OUT: Final = "next_compress"
"""The response attribute that, set to `False`, keeps that response uncompressed.

A page sets it and the compressors read it, so neither area imports the other.
"""

STREAM_OWNERS: Final = ("self", "middleware")
"""Who compresses a next.dj document stream, the stream itself or the middleware."""

TOPIC_MODES: Final = ("off", "reset", "shared")
"""How a realtime topic stream shares the compression window across its events."""

type Usable = Callable[[object], bool]

_MEDIA: Final = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]*/(?:\*|[a-z0-9][a-z0-9!#$&^_.+-]*)"
)


def _flag(value: object) -> TypeGuard[bool]:
    return isinstance(value, bool)


def _size(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _millis(value: object) -> TypeGuard[float]:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and value >= 0


def _texts(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) and item for item in value
    )


def _codec_numbers(value: object) -> TypeGuard[Mapping[str, int]]:
    return isinstance(value, Mapping) and all(
        isinstance(key, str)
        and key
        and isinstance(number, int)
        and not isinstance(number, bool)
        for key, number in value.items()
    )


def _media_types(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) and _MEDIA.fullmatch(item) is not None for item in value
    )


def _choice(values: tuple[str, ...]) -> Callable[[object], TypeGuard[str]]:
    def usable(value: object) -> TypeGuard[str]:
        return isinstance(value, str) and value in values

    return usable


def _optional_path(value: object) -> TypeGuard[str | None]:
    return value is None or (isinstance(value, str) and bool(value))


SHAPES: Final[Mapping[str, tuple[Usable, str]]] = MappingProxyType(
    {
        "ENABLED": (_flag, "True or False"),
        "BACKENDS": (_texts, "a list of dotted paths"),
        "ORDER": (_texts, "a list of codec tokens"),
        "LEVELS": (_codec_numbers, "a dict of codec tokens to integers"),
        "WINDOWS": (_codec_numbers, "a dict of codec tokens to integers"),
        "MIN_SIZE": (_size, "an integer from 0"),
        "CONTENT_TYPES": (
            _media_types,
            "a list of lower-case media types such as 'text/html' or 'text/*'",
        ),
        "STREAMS": (_choice(STREAM_OWNERS), "'self' or 'middleware'"),
        "SSE": (_flag, "True or False"),
        "SSE_TOPICS": (_choice(TOPIC_MODES), "'off', 'reset' or 'shared'"),
        "PADDING": (_size, "an integer from 0"),
        "COALESCE_MS": (_millis, "a number of milliseconds from 0"),
        "OFFLOAD_BYTES": (_size, "an integer from 0"),
        "RESPECT_NO_TRANSFORM": (_flag, "True or False"),
        "EXCLUDE": (_optional_path, "a dotted path or None"),
    }
)
"""Every `COMPRESSION` key with the predicate it passes and the words naming it."""


@dataclass(frozen=True, slots=True)
class Misfit:
    """One `COMPRESSION` value outside its shape, with what the key expects."""

    key: str
    expected: str
    found: str


def _shown(value: object) -> str:
    """Return how a report names a misfit value, a scalar by its repr."""
    if value is None or isinstance(value, int | float | str):
        return repr(value)
    return f"a {type(value).__name__}"


def misfits(scope: Mapping[str, object]) -> list[Misfit]:
    """Return every known key of `scope` whose value is outside its shape."""
    return [
        Misfit(key, expected, _shown(scope[key]))
        for key, (usable, expected) in SHAPES.items()
        if key in scope and not usable(scope[key])
    ]


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
    streams: str
    sse: bool
    sse_topics: str
    padding: int
    coalesce_ms: float
    offload_bytes: int
    respect_no_transform: bool
    exclude: str | None


def _pick[T](
    scope: Mapping[str, object], key: str, usable: Callable[[object], TypeGuard[T]]
) -> T:
    """Return the value of `key` when `usable` passes it, its default when omitted."""
    value = scope.get(key)
    if key in scope and usable(value):
        return value
    default: T = FROZEN_DEFAULTS[SCOPE][key]
    return default


def _codecs(scope: Mapping[str, object], key: str) -> Mapping[str, int]:
    """Return the codec numbers of `key` laid over its default mapping."""
    default: Mapping[str, int] = FROZEN_DEFAULTS[SCOPE][key]
    value = scope.get(key)
    if key in scope and _codec_numbers(value):
        return MappingProxyType({**default, **value})
    return default


def read_settings(scope: Mapping[str, object]) -> CompressionSettings:
    """Return the settings `scope` asks for, the default replacing each misfit."""
    return CompressionSettings(
        enabled=_pick(scope, "ENABLED", _flag),
        backends=tuple(_pick(scope, "BACKENDS", _texts)),
        order=tuple(_pick(scope, "ORDER", _texts)),
        levels=_codecs(scope, "LEVELS"),
        windows=_codecs(scope, "WINDOWS"),
        min_size=_pick(scope, "MIN_SIZE", _size),
        content_types=tuple(_pick(scope, "CONTENT_TYPES", _media_types)),
        streams=_pick(scope, "STREAMS", _choice(STREAM_OWNERS)),
        sse=_pick(scope, "SSE", _flag),
        sse_topics=_pick(scope, "SSE_TOPICS", _choice(TOPIC_MODES)),
        padding=_pick(scope, "PADDING", _size),
        coalesce_ms=float(_pick(scope, "COALESCE_MS", _millis)),
        offload_bytes=_pick(scope, "OFFLOAD_BYTES", _size),
        respect_no_transform=_pick(scope, "RESPECT_NO_TRANSFORM", _flag),
        exclude=_pick(scope, "EXCLUDE", _optional_path),
    )


class _Memo:
    """Hold the settings read from the merged `COMPRESSION` mapping held now."""

    __slots__ = ("held", "settings")

    def __init__(self) -> None:
        """Start before the first read."""
        self.held: object = None
        self.settings: CompressionSettings | None = None

    def get(self) -> CompressionSettings:
        """Return the settings of the current mapping, reading them when it moved."""
        held = next_framework_settings.COMPRESSION
        settings = self.settings
        if settings is None or self.held is not held:
            scope = held if isinstance(held, Mapping) else {}
            settings = read_settings(scope)
            self.held, self.settings = held, settings
        return settings


_memo = _Memo()


def compression_settings() -> CompressionSettings:
    """Return the `COMPRESSION` settings, read again once the merged mapping moves."""
    return _memo.get()


__all__ = [
    "OPT_OUT",
    "SCOPE",
    "SHAPES",
    "STREAM_OWNERS",
    "TOPIC_MODES",
    "CompressionSettings",
    "Misfit",
    "compression_settings",
    "misfits",
    "read_settings",
]
