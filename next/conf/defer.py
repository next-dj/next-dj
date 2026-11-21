"""The shape of the `DEFER` scope and the reads of its values with their defaults."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, TypeGuard, cast

from next.defer import MAX_DURATION, MIN_KEY_LENGTH, BoundaryOptionError, parse_duration

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings


SCOPE: Final = "DEFER"

type Usable = Callable[[object], bool]


@dataclass(frozen=True, slots=True)
class Shape:
    """What one key accepts, the predicate and the words a report names it by."""

    usable: Usable
    expected: str


@dataclass(frozen=True, slots=True)
class Section:
    """A nested mapping, its keys known unless a backend other than `default` owns it.

    `below` pairs a key with the one its integer value must stay under.
    """

    keys: Mapping[str, Shape | Section]
    default: str | None = field(default=None)
    below: tuple[tuple[str, str], ...] = ()


def _count(least: int) -> Usable:
    def usable(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= least

    return usable


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


def _choice(values: tuple[object, ...]) -> Usable:
    def usable(value: object) -> bool:
        return any(type(value) is type(choice) and value == choice for choice in values)

    return usable


def _flag(value: object) -> bool:
    return isinstance(value, bool)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _texts(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(map(_text, value))


_DOTTED: Final = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", re.ASCII)


def _dotted(value: object) -> bool:
    return isinstance(value, str) and _DOTTED.fullmatch(value) is not None


def _keys(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return _dotted(value)
    return (
        _texts(value)
        and bool(value)
        and all(len(key) >= MIN_KEY_LENGTH for key in value)
    )


_MARK_KINDS: Final = frozenset({"WSGI", "ASGI"})
_MARK_LISTS: Final = frozenset({"KEYS", "PREFIXES"})


def _mark_lists(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.keys() <= _MARK_LISTS
        and all(_texts(entry) for entry in value.values())
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
    return _count(1)(value) or _dotted(value)


def _ratio(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and 0 < value <= 1


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


_FLAG: Final = Shape(_flag, "True or False")
_PATH: Final = Shape(_text, "a dotted path")
_PATHS: Final = Shape(_texts, "a list of dotted paths")
_CAP: Final = Shape(_count(1), "an integer from 1")
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
                "SQLITE": Shape(
                    _choice(("foreground", "parallel")), "'foreground' or 'parallel'"
                ),
                "THREAD_LOCAL_MIDDLEWARE": _PATHS,
                "PLACEMENT": Section(
                    {
                        "MODE": Shape(
                            _choice(("adaptive", "pool")), "'adaptive' or 'pool'"
                        ),
                        "HANDOFF": _WAIT,
                        "CPU_RATIO": Shape(_ratio, "a number above 0 up to 1"),
                        "CPU_MAX": _WAIT,
                        "EXPLORE": Shape(_count(0), "an integer from 0"),
                    }
                ),
            },
        ),
        "POLICY": _backend(
            "next.suspense.DefaultStreamPolicy",
            {
                "STREAM": _FLAG,
                "BOTS": Shape(_pattern, "a regular expression or None"),
                "SYNC_WORKERS": Shape(
                    _choice(("inline", "stream")), "'inline' or 'stream'"
                ),
            },
        ),
        "TRANSPORT": _backend(
            "next.suspense.DefaultStreamTransport",
            {
                "INCREMENTAL": Shape(_optional(_flag), "True, False or None"),
                "CONCURRENT": Shape(_optional(_flag), "True, False or None"),
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
                "BUCKET": Shape(_optional(_count(0)), "an integer from 0 or None"),
                "MAX_BYTES": _CAP,
                "MAX_STORED_BYTES": _CAP,
                "CACHE_ALIAS": Shape(_text, "a cache alias"),
                "KEYS": Shape(
                    _keys,
                    "None, a dotted path or a list of keys of "
                    f"{MIN_KEY_LENGTH} characters or more",
                ),
                "EPOCH": Shape(_count(0), "an integer from 0"),
            },
            below=(("BUCKET", "MAX_AGE"),),
        ),
        "CONNECTION_STATE": _PATHS,
        "THREAD_STATE": _PATHS,
        "ADMISSION": _PATH,
        "DEADLINE": Shape(
            _seconds(positive=True), "a duration above 0, in seconds or as '2s'"
        ),
        "DEADLINE_TO_DB": Shape(
            _choice(("state", "always", False)), "'state', 'always' or False"
        ),
        "DB_TIMEOUT_FLOOR": _WAIT,
        "WRITE_GRACE": _WAIT,
        "REQUEST_CONCURRENCY": _CAP,
        "MAX_BOUNDARIES": _CAP,
        "MAX_PER_SITE": _CAP,
        "MAX_DEPTH": _CAP,
        "MAX_INSTANCES": _CAP,
        "MAX_STREAMS": Shape(
            lambda value: value == "auto" or _count(1)(value),
            "'auto' or an integer from 1",
        ),
        "BATCH_WINDOW": _WAIT,
        "LOADER_MAX_BATCH": _CAP,
        "LOADING": Shape(_optional(_text), "a template path or None"),
        "ERROR": Shape(_optional(_text), "a template path or None"),
        "NOSCRIPT": _FLAG,
        "ERRORS": Section(
            {
                "RAISE": _FLAG,
                "REPORTER": Shape(_optional(_text), "a dotted path or None"),
                "ATOMIC": Shape(
                    _choice(("writes", True, False)), "'writes', True or False"
                ),
                "KINDS": Shape(
                    _choice(("detailed", "opaque")), "'detailed' or 'opaque'"
                ),
                "DEBUG_DETAIL": _FLAG,
            }
        ),
        "MORE": Section({"MAX_RESTORE": _CAP}),
    }
)
"""Every `DEFER` key with what it accepts, nested the way the scope nests."""


@dataclass(frozen=True, slots=True)
class Misfit:
    """One `DEFER` value outside its shape, or keys its mapping does not know.

    `unknown` lists the stray keys of the mapping at `path`, empty for a bad value.
    """

    path: tuple[str, ...]
    expected: str = ""
    found: str = ""
    unknown: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()


def _shown(value: object) -> str:
    """Return how a report names a misfit value, a scalar by its repr."""
    if value is None or isinstance(value, int | float | str):
        return repr(value)
    return f"a {type(value).__name__}"


def _owned_by_default(section: Section, parent: Mapping[str, object]) -> bool:
    """Whether the keys of `section` are known, as no foreign backend owns them."""
    return section.default is None or parent.get("BACKEND", section.default) == (
        section.default
    )


_ABSENT: Final = object()


def _at(mapping: object, path: tuple[str, ...]) -> object:
    """Return the value `path` reaches through nested mappings, else `_ABSENT`."""
    for key in path:
        if not isinstance(mapping, Mapping) or key not in mapping:
            return _ABSENT
        mapping = mapping[key]
    return mapping


def _bound(value: object, shape: Shape | Section) -> int | None:
    """Return `value` when it is a usable integer of `shape`, else `None`."""
    if isinstance(shape, Section) or not shape.usable(value):
        return None
    return value if isinstance(value, int) else None


def _out_of_order(
    scope: Mapping[str, object], section: Section, path: tuple[str, ...]
) -> list[Misfit]:
    """Return a misfit for each integer at or above the key it must stay under."""
    found: list[Misfit] = []
    for low, high in section.below:
        value = _bound(scope.get(low), section.keys[low])
        default = _at(FROZEN_DEFAULTS[SCOPE], (*path, high))
        ceiling = _bound(scope.get(high, default), section.keys[high])
        if value is not None and ceiling is not None and value >= ceiling:
            expected = f"an integer below {high}, which is {ceiling}, or None"
            found.append(Misfit((*path, low), expected, _shown(value)))
    return found


def misfits(
    scope: Mapping[str, object],
    section: Section = SHAPE,
    path: tuple[str, ...] = (),
    parent: Mapping[str, object] | None = None,
) -> list[Misfit]:
    """Return every value of `scope` outside its shape and every key it does not know.

    The `OPTIONS` of a foreign backend belong to that backend, so they pass unread.
    """
    if not _owned_by_default(section, parent or {}):
        return []
    found: list[Misfit] = []
    unknown = tuple(sorted(str(key) for key in scope if key not in section.keys))
    if unknown:
        found.append(Misfit(path, unknown=unknown, allowed=tuple(sorted(section.keys))))
    for key, shape in section.keys.items():
        if key not in scope:
            continue
        value = scope[key]
        where = (*path, key)
        if isinstance(shape, Shape):
            if not shape.usable(value):
                found.append(Misfit(where, shape.expected, _shown(value)))
        elif not isinstance(value, Mapping):
            found.append(Misfit(where, "a dict", _shown(value)))
        else:
            found.extend(misfits(value, shape, where, scope))
    found.extend(_out_of_order(scope, section, path))
    return found


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
    if not _owned_by_default(options, tokens):
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

    held: object
    scope: Mapping[str, object]
    refused: frozenset[tuple[str, ...]]
    values: dict[tuple[str, ...], object] = field(default_factory=dict)


class _Memo:
    """Hold the reads of the merged `DEFER` mapping the settings hold now."""

    __slots__ = ("reads",)

    def __init__(self) -> None:
        """Start before the first read."""
        self.reads: _Reads | None = None

    def get(self) -> _Reads:
        """Return the reads of the current mapping, starting them when it moved."""
        held = next_framework_settings.DEFER
        reads = self.reads
        if reads is None or reads.held is not held:
            scope = held if isinstance(held, Mapping) else {}
            refused = frozenset(m.path for m in misfits(scope) if not m.unknown)
            reads = _Reads(held, scope, refused)
            self.reads = reads
        return reads


_memo = _Memo()


def defer_option(*path: str) -> object:
    """Return `DEFER` at `path`, each nested key the project omits or misfits defaulted.

    The reads are held until the settings replace the merged `DEFER` mapping.
    """
    reads = _memo.get()
    values = reads.values
    if path not in values:
        values[path] = _option(reads.scope, path, reads.refused)
    return values[path]


def defer_count(*path: str) -> int:
    """Return the integer `DEFER` value at `path`, the default replacing a misfit."""
    return cast("int", defer_option(*path))


def defer_seconds(*path: str) -> float:
    """Return the duration at `path` in seconds, the default replacing a misfit."""
    return parse_duration(defer_option(*path))


def defer_template(*path: str) -> str | None:
    """Return the template path `DEFER` names at `path`, the default for a misfit."""
    return cast("str | None", defer_option(*path))


__all__ = [
    "SCOPE",
    "SHAPE",
    "Misfit",
    "Section",
    "Shape",
    "defer_count",
    "defer_option",
    "defer_seconds",
    "defer_template",
    "long_token_age",
    "misfits",
]
