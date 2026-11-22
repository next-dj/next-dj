"""The shape of the `DEFER` scope and the reads of its values with their defaults."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, TypeGuard

from next.caches import IdentityMemo
from next.defer import MAX_DURATION, MIN_KEY_LENGTH, BoundaryOptionError, parse_duration

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import (
    EMPTY,
    Section,
    Shape,
    Usable,
    choice,
    count,
    flag,
    misfits,
    optional_text,
    owned,
    text,
    texts,
)


SCOPE: Final = "DEFER"

type DeadlineMode = Literal["state", "always", False]
type PlacementMode = Literal["adaptive", "pool"]


def _seconds(*, positive: bool = False) -> Usable:
    def usable(value: object) -> bool:
        try:
            seconds = parse_duration(value)
        except BoundaryOptionError:
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


def _keys(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return _dotted(value)
    return (
        texts(value)
        and bool(value)
        and all(len(key) >= MIN_KEY_LENGTH for key in value)
    )


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


def fraction(value: object) -> TypeGuard[float]:
    """Whether `value` is a number above 0 up to 1, never a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and 0 < value <= 1


_DEADLINE_MODES: Final = choice(("state", "always", False))
_PLACEMENT_MODES: Final = choice(("adaptive", "pool"))
_ERRORS_ATOMIC: Final = choice(("writes", True, False))


def deadline_choice(value: object) -> TypeGuard[DeadlineMode]:
    """Whether `value` is a `DEADLINE_TO_DB` mode."""
    return _DEADLINE_MODES.usable(value)


def placement_choice(value: object) -> TypeGuard[PlacementMode]:
    """Whether `value` is a placement `MODE`."""
    return _PLACEMENT_MODES.usable(value)


def atomic_choice(value: object) -> TypeGuard[bool | Literal["writes"]]:
    """Whether `value` is an atomic mode, as `ERRORS` and `TRANSACTIONS` take it."""
    return _ERRORS_ATOMIC.usable(value)


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


def _backend(
    path: str,
    options: Mapping[str, Shape | Section],
    below: tuple[tuple[str, str], ...] = (),
) -> Section:
    return Section(
        {"BACKEND": _PATH, "OPTIONS": Section(options, default=path, below=below)}
    )


SHAPE: Final = Section(
    {
        "EXECUTOR": _backend(
            "next.suspense.LaneExecutor",
            {
                "MAX_WORKERS": _CAP,
                "PER_REQUEST": _CAP,
                "QUEUE_TIMEOUT": _WAIT,
                "MAX_QUEUE": _CAP,
                "MAX_FOREGROUND": _CAP,
                "CONN_IDLE": _WAIT,
                "PARALLEL": _FLAG,
                "SQLITE": choice(("foreground", "parallel")),
                "THREAD_LOCAL_MIDDLEWARE": _PATHS,
                "PLACEMENT": Section(
                    {
                        "MODE": _PLACEMENT_MODES,
                        "HANDOFF": _WAIT,
                        "CPU_RATIO": Shape(fraction, "a number above 0 up to 1"),
                        "CPU_MAX": _WAIT,
                        "EXPLORE": Shape(count(0), "an integer from 0"),
                    }
                ),
            },
        ),
        "POLICY": _backend(
            "next.suspense.DefaultStreamPolicy",
            {
                "STREAM": _FLAG,
                "BOTS": Shape(_pattern, "a regular expression or None"),
                "SYNC_WORKERS": choice(("inline", "stream")),
            },
        ),
        "TRANSPORT": _backend(
            "next.suspense.DefaultStreamTransport",
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
            "next.suspense.StackedTokenBackend",
            {
                "CODEC": _PATH,
                "STORAGE": _PATH,
                "SEAL": _PATH,
                "BINDING": _PATH,
                "TRANSPORT": _PATH,
                "MAX_AGE": Shape(_age, "an integer from 1 or a dotted path"),
                "BUCKET": Shape(_optional(count(0)), "an integer from 0 or None"),
                "MAX_BYTES": _CAP,
                "MAX_STORED_BYTES": _CAP,
                "CACHE_ALIAS": Shape(text, "a cache alias"),
                "KEYS": Shape(
                    _keys,
                    "None, a dotted path or a list of keys of "
                    f"{MIN_KEY_LENGTH} characters or more",
                ),
                "EPOCH": Shape(count(0), "an integer from 0"),
            },
            below=(("BUCKET", "MAX_AGE"),),
        ),
        "CONNECTION_STATE": _PATHS,
        "THREAD_STATE": _PATHS,
        "ADMISSION": _PATH,
        "DEADLINE": Shape(
            _seconds(positive=True), "a duration above 0, in seconds or as '2s'"
        ),
        "DEADLINE_TO_DB": _DEADLINE_MODES,
        "DB_TIMEOUT_FLOOR": _WAIT,
        "WRITE_GRACE": _WAIT,
        "REQUEST_CONCURRENCY": _CAP,
        "MAX_BOUNDARIES": _CAP,
        "MAX_PER_SITE": _CAP,
        "MAX_DEPTH": _CAP,
        "MAX_INSTANCES": _CAP,
        "MAX_STREAMS": Shape(_streams, "'auto' or an integer from 1"),
        "BATCH_WINDOW": _WAIT,
        "LOADER_MAX_BATCH": _CAP,
        "LOADING": Shape(optional_text, "a template path or None"),
        "ERROR": Shape(optional_text, "a template path or None"),
        "NOSCRIPT": _FLAG,
        "ERRORS": Section(
            {
                "RAISE": _FLAG,
                "REPORTER": Shape(optional_text, "a dotted path or None"),
                "ATOMIC": _ERRORS_ATOMIC,
                "KINDS": choice(("detailed", "opaque")),
                "DEBUG_DETAIL": _FLAG,
            }
        ),
        "MORE": Section({"MAX_RESTORE": _CAP}),
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


def _option(
    scope: Mapping[str, object],
    path: tuple[str, ...],
    refused: frozenset[tuple[str, ...]],
) -> object:
    """Return the value at `path`, a section merged over its defaults key by key."""
    default = _at(FROZEN_DEFAULTS[SCOPE], path)
    held = _at(scope, path)
    if isinstance(_shape_at(path), Section) or isinstance(default, Mapping):
        keys = dict.fromkeys(default if isinstance(default, Mapping) else ())
        keys.update(dict.fromkeys(held if isinstance(held, Mapping) else ()))
        return MappingProxyType(
            {key: _option(scope, (*path, key), refused) for key in keys}
        )
    if held is _ABSENT or path in refused:
        return None if default is _ABSENT else default
    return held


@dataclass(frozen=True, slots=True)
class _Reads:
    """The reads of one merged `DEFER` mapping, replaced whole along with it."""

    scope: Mapping[str, object]
    refused: frozenset[tuple[str, ...]]
    values: dict[tuple[str, ...], object] = field(default_factory=dict)


def _reads() -> _Reads:
    """Start the reads of the merged `DEFER` mapping, its misfit paths refused."""
    held = next_framework_settings.DEFER
    scope = held if isinstance(held, Mapping) else EMPTY
    found = misfits(scope, SHAPE, FROZEN_DEFAULTS[SCOPE])
    return _Reads(scope, frozenset(m.path for m in found if not m.unknown))


_READS: Final[IdentityMemo[_Reads]] = IdentityMemo()


def defer_option(*path: str) -> object:
    """Return `DEFER` at `path`, each nested key the project omits or misfits defaulted.

    The reads are held until the settings replace the merged `DEFER` mapping.
    """
    reads = _READS.get(next_framework_settings.DEFER, _reads)
    values = reads.values
    if path not in values:
        values[path] = _option(reads.scope, path, reads.refused)
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
    """Return the integer `DEFER` value at `path`, the default replacing a misfit."""
    return defer_read(_COUNT, *path)


def defer_seconds(*path: str) -> float:
    """Return the duration at `path` in seconds, the default replacing a misfit."""
    return parse_duration(defer_option(*path))


def defer_template(*path: str) -> str | None:
    """Return the template path `DEFER` names at `path`, the default for a misfit."""
    return defer_read(optional_text, *path)


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
    """Return the integer `key` of a backend's `OPTIONS`, as `defer_count` reads one."""
    value = backend_option(options, section, key)
    return _typed(_COUNT, value, (section, "OPTIONS", key))


def backend_paths(key: Literal["THREAD_STATE", "CONNECTION_STATE"]) -> tuple[str, ...]:
    """Return the dotted paths of the `THREAD_STATE` or `CONNECTION_STATE` backends."""
    return tuple(defer_read(texts, key))


def deadline_mode() -> DeadlineMode:
    """Return `DEADLINE_TO_DB`, how the job deadline reaches the database."""
    return defer_read(deadline_choice, "DEADLINE_TO_DB")


def timeout_floor() -> float:
    """Return `DB_TIMEOUT_FLOOR` in seconds, the least timeout sent to the database."""
    return defer_seconds("DB_TIMEOUT_FLOOR")


def parallel() -> bool:
    """Return `EXECUTOR["OPTIONS"]["PARALLEL"]`, False keeping jobs on the request."""
    return defer_read(flag, "EXECUTOR", "OPTIONS", "PARALLEL")


def sqlite_parallel() -> bool:
    """Whether `EXECUTOR["OPTIONS"]["SQLITE"]` lets SQLite jobs run on the pool."""
    return defer_option("EXECUTOR", "OPTIONS", "SQLITE") == "parallel"


def thread_local_middleware() -> tuple[str, ...]:
    """Return the middleware `EXECUTOR["OPTIONS"]["THREAD_LOCAL_MIDDLEWARE"]` lists."""
    return tuple(defer_read(texts, "EXECUTOR", "OPTIONS", "THREAD_LOCAL_MIDDLEWARE"))


__all__ = [
    "SCOPE",
    "SHAPE",
    "DeadlineMode",
    "PlacementMode",
    "atomic_choice",
    "backend_count",
    "backend_option",
    "backend_paths",
    "deadline_choice",
    "deadline_mode",
    "defer_count",
    "defer_option",
    "defer_read",
    "defer_seconds",
    "defer_template",
    "entry_options",
    "fraction",
    "long_token_age",
    "parallel",
    "placement_choice",
    "sqlite_parallel",
    "thread_local_middleware",
    "timeout_floor",
]
