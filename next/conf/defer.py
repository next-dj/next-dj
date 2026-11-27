"""The shape of the `DEFER` scope and the reads of its values with their defaults."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, TypeGuard

from next.caches import IdentityMemo
from next.errors import DurationError
from next.utils import MAX_DURATION, parse_duration

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import (
    EMPTY,
    Section,
    Shape,
    Usable,
    choice,
    count,
    enforce,
    flag,
    owned,
    text,
    texts,
)


SCOPE: Final = "DEFER"

type DeadlineMode = Literal["state", "always", False]


def _seconds(*, positive: bool = False) -> Usable:
    def usable(value: object) -> bool:
        try:
            seconds = parse_duration(value)
        except DurationError:
            return False
        return seconds > 0 or not positive

    return usable


def _optional(inner: Usable) -> Usable:
    def usable(value: object) -> bool:
        return value is None or inner(value)

    return usable


_DOTTED: Final = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", re.ASCII)
_ONE_UP: Final = count(1)


def _dotted(value: object) -> bool:
    return isinstance(value, str) and _DOTTED.fullmatch(value) is not None


_MARK_KINDS: Final = frozenset({"WSGI", "ASGI"})
_MARK_LISTS: Final = frozenset({"KEYS", "PREFIXES"})


def _mark_lists(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.keys() <= _MARK_LISTS
        and all(texts(entry) for entry in value.values())
    )


def _marks(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, Mapping)
        and value.keys() <= _MARK_KINDS
        and all(_mark_lists(entry) for entry in value.values())
    )


def _age(value: object) -> bool:
    return _ONE_UP(value) or _dotted(value)


def _streams(value: object) -> bool:
    return value == "auto" or _ONE_UP(value)


_DEADLINE_MODES: Final = choice(("state", "always", False))


def deadline_choice(value: object) -> TypeGuard[DeadlineMode]:
    """Whether `value` is a `DEADLINE_TO_DB` mode."""
    return _DEADLINE_MODES.usable(value)


def _pattern(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        re.compile(value)
    except re.error:
        return False
    return True


_FLAG: Final = Shape(flag, "True or False")
_PATH: Final = Shape(text, "a dotted path")
_PATHS: Final = Shape(texts, "a list of dotted paths")
_CAP: Final = Shape(_ONE_UP, "an integer from 1")
_WAIT: Final = Shape(_seconds(), "a duration from 0, in seconds or as '2s'")


_NO_SHAPES: Final[Mapping[str, Shape | Section]] = MappingProxyType({})


def _backend(
    path: str,
    options: Mapping[str, Shape | Section],
    beside: Mapping[str, Shape | Section] = _NO_SHAPES,
) -> Section:
    return Section(
        {"BACKEND": _PATH, "OPTIONS": Section(options, default=path), **beside}
    )


SHAPE: Final = Section(
    {
        "EXECUTOR": _backend(
            "next.defer.LaneExecutor",
            {
                "MAX_WORKERS": _CAP,
                "MAX_QUEUE": _CAP,
                "CONN_IDLE": _WAIT,
                "SHUTDOWN_TIMEOUT": _WAIT,
            },
            {
                "PER_REQUEST": _CAP,
                "QUEUE_TIMEOUT": _WAIT,
                "SHED_DELAY": Shape(
                    _seconds(positive=True), "a duration above 0, in seconds or as '2s'"
                ),
                "MAX_REQUEST_JOBS": _CAP,
                "PARALLEL": _FLAG,
                "SQLITE": choice(("request", "parallel")),
                "THREAD_LOCAL_MIDDLEWARE": _PATHS,
            },
        ),
        "DELIVERY": _backend(
            "next.defer.DefaultDeliveryPolicy",
            {
                "STREAM": _FLAG,
                "BOTS": Shape(_pattern, "a regular expression or None"),
                "SYNC_WORKERS": choice(("inline", "stream")),
            },
        ),
        "SERVER": _backend(
            "next.defer.DefaultServerProbe",
            {
                "INCREMENTAL": Shape(_optional(flag), "True, False or None"),
                "CONCURRENT": Shape(_optional(flag), "True, False or None"),
                "BUFFERED_MARKS": Shape(
                    _marks,
                    "None or a dict of WSGI and ASGI dicts, each of KEYS and "
                    "PREFIXES lists of strings",
                ),
            },
        ),
        "TOKENS": _backend(
            "next.defer.StackedTokenBackend",
            {
                "STORAGE": _PATH,
                "SEAL": _PATH,
                "BINDING": _PATH,
                "CARRIER": choice(("header", "query", "body")),
                "MAX_AGE": Shape(_age, "an integer from 1 or a dotted path"),
                "MAX_BYTES": _CAP,
                "MAX_STORED_BYTES": _CAP,
                "CACHE_ALIAS": Shape(text, "a cache alias"),
                "EPOCH": Shape(count(0), "an integer from 0"),
            },
        ),
        "CONNECTION_STATE": _PATHS,
        "THREAD_STATE": _PATHS,
        "ADMISSION": _backend("next.defer.QueueDelay", {}),
        "DEADLINE": Shape(
            _seconds(positive=True), "a duration above 0, in seconds or as '2s'"
        ),
        "DEADLINE_TO_DB": _DEADLINE_MODES,
        "WRITE_GRACE": _WAIT,
        "WRITE_TIMEOUT": Shape(
            _seconds(positive=True), "a duration above 0, in seconds or as '2s'"
        ),
        "REQUEST_CONCURRENCY": _CAP,
        "MAX_BOUNDARIES": _CAP,
        "MAX_PER_SITE": _CAP,
        "MAX_DEPTH": _CAP,
        "MAX_INSTANCES": _CAP,
        "MAX_STREAMS": Shape(_streams, "'auto' or an integer from 1"),
        "NOSCRIPT": _FLAG,
    }
)
"""Every `DEFER` key with what it accepts, nested the way the scope nests."""


_ABSENT: Final = object()


def _at(mapping: object, path: tuple[str, ...]) -> object:
    """Return the value `path` reaches through nested mappings, else `_ABSENT`."""
    for key in path:
        if not isinstance(mapping, Mapping) or key not in mapping:
            return _ABSENT
        mapping = mapping[key]
    return mapping


def _shape_at(path: tuple[str, ...]) -> Shape | Section | None:
    shape: Shape | Section | None = SHAPE
    for key in path:
        if not isinstance(shape, Section):
            return None
        shape = shape.keys.get(key)
    return shape


def long_token_age(scope: Mapping[str, object]) -> int | None:
    """Return the `TOKENS` `MAX_AGE` the scope writes above one day, else `None`."""
    tokens = scope.get("TOKENS")
    options = _shape_at(("TOKENS", "OPTIONS"))
    if not isinstance(tokens, Mapping) or not isinstance(options, Section):
        return None
    if not owned(options, tokens):
        return None
    age = _at(tokens, ("OPTIONS", "MAX_AGE"))
    if isinstance(age, int) and not isinstance(age, bool) and age > MAX_DURATION:
        return age
    return None


def _option(scope: Mapping[str, object], path: tuple[str, ...]) -> object:
    """Return the value at `path`, a section merged over its defaults key by key."""
    default = _at(FROZEN_DEFAULTS[SCOPE], path)
    held = _at(scope, path)
    if isinstance(_shape_at(path), Section) or isinstance(default, Mapping):
        keys = dict.fromkeys(default if isinstance(default, Mapping) else ())
        keys.update(dict.fromkeys(held if isinstance(held, Mapping) else ()))
        return MappingProxyType({key: _option(scope, (*path, key)) for key in keys})
    if held is _ABSENT:
        return None if default is _ABSENT else default
    return held


@dataclass(frozen=True, slots=True)
class _Reads:
    """The reads of one merged `DEFER` mapping, replaced whole along with it."""

    scope: Mapping[str, object]
    values: dict[tuple[str, ...], object] = field(default_factory=dict)


def _reads() -> _Reads:
    """Start the reads of the merged `DEFER` mapping, refusing a value off its shape."""
    held = next_framework_settings.DEFER
    scope = held if isinstance(held, Mapping) else EMPTY
    enforce(f"NEXT_FRAMEWORK[{SCOPE!r}]", scope, SHAPE, FROZEN_DEFAULTS[SCOPE])
    return _Reads(scope)


_READS: Final[IdentityMemo[_Reads]] = IdentityMemo()


def defer_option(*path: str) -> object:
    """Return `DEFER` at `path`, each nested key the project omits at its default.

    The reads are held until the settings replace the merged `DEFER` mapping.
    """
    reads = _READS.get(next_framework_settings.DEFER, _reads)
    values = reads.values
    if path not in values:
        values[path] = _option(reads.scope, path)
    return values[path]


def _typed[T](
    usable: Callable[[object], TypeGuard[T]], value: object, path: tuple[str, ...]
) -> T:
    """Return `value` as `usable` types it, refusing one the reader disagrees with."""
    if not usable(value):
        where = "".join(f"[{key!r}]" for key in path)
        msg = f"DEFER{where} holds {value!r}, which its reader does not take"
        raise TypeError(msg)
    return value


def defer_read[T](usable: Callable[[object], TypeGuard[T]], *path: str) -> T:
    """Return the `DEFER` value at `path` as the predicate of its shape types it.

    The value already passed its shape, so a refusal is a reader that disagrees with it.
    """
    return _typed(usable, defer_option(*path), path)


_COUNT: Final = count(0)


def defer_count(*path: str) -> int:
    """Return the integer `DEFER` value at `path`."""
    return defer_read(_COUNT, *path)


def defer_seconds(*path: str) -> float:
    """Return the duration at `path` in seconds."""
    return parse_duration(defer_option(*path))


def entry_options(config: Mapping[str, object] | None) -> Mapping[str, object]:
    """Return the `OPTIONS` of a backend settings entry, empty when it holds none."""
    options = (config or EMPTY).get("OPTIONS")
    return options if isinstance(options, Mapping) else EMPTY


def backend_option(options: Mapping[str, object], section: str, key: str) -> object:
    """Return `key` of a backend's `OPTIONS`, the `DEFER` value when they omit it."""
    if key in options:
        return options[key]
    return defer_option(section, "OPTIONS", key)


def backend_count(options: Mapping[str, object], section: str, key: str) -> int:
    """Return the integer `key` of backend `OPTIONS`, from `DEFER` when they omit it."""
    return _typed(
        _COUNT, backend_option(options, section, key), (section, "OPTIONS", key)
    )


def backend_seconds(options: Mapping[str, object], section: str, key: str) -> float:
    """Return the duration `key` of a backend's `OPTIONS` in seconds."""
    return parse_duration(backend_option(options, section, key))


def backend_paths(key: Literal["THREAD_STATE", "CONNECTION_STATE"]) -> tuple[str, ...]:
    """Return the dotted paths of the `THREAD_STATE` or `CONNECTION_STATE` backends."""
    return tuple(defer_read(texts, key))


def deadline_mode() -> DeadlineMode:
    """Return `DEADLINE_TO_DB`, how the job deadline reaches the database."""
    return defer_read(deadline_choice, "DEADLINE_TO_DB")


def parallel() -> bool:
    """Return `EXECUTOR["PARALLEL"]`, False keeping jobs on the request."""
    return defer_read(flag, "EXECUTOR", "PARALLEL")


def sqlite_parallel() -> bool:
    """Whether `EXECUTOR["SQLITE"]` lets SQLite jobs run on the pool."""
    return defer_option("EXECUTOR", "SQLITE") == "parallel"


def thread_local_middleware() -> tuple[str, ...]:
    """Return the middleware `EXECUTOR["THREAD_LOCAL_MIDDLEWARE"]` lists."""
    return tuple(defer_read(texts, "EXECUTOR", "THREAD_LOCAL_MIDDLEWARE"))


__all__ = [
    "SCOPE",
    "SHAPE",
    "DeadlineMode",
    "backend_count",
    "backend_option",
    "backend_paths",
    "backend_seconds",
    "deadline_choice",
    "deadline_mode",
    "defer_count",
    "defer_option",
    "defer_read",
    "defer_seconds",
    "entry_options",
    "long_token_age",
    "parallel",
    "sqlite_parallel",
    "thread_local_middleware",
]
