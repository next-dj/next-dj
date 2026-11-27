"""The shape of a settings scope, the walk naming its misfits and the typed pick."""

import enum
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, TypeGuard

from next.errors import SettingShapeError


type Usable = Callable[[object], bool]

EMPTY: Final[Mapping[str, object]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Shape:
    """What one key accepts, the predicate and the words a report names it by."""

    usable: Usable
    expected: str


@dataclass(frozen=True, slots=True)
class Section:
    """A nested mapping, its keys known unless a backend other than `default` owns it.

    `below` pairs a key with the one its integer stays under, `other` admits a non-dict.
    """

    keys: Mapping[str, "Shape | Section"]
    default: str | None = None
    below: tuple[tuple[str, str], ...] = ()
    other: Shape | None = None


@dataclass(frozen=True, slots=True)
class Misfit:
    """One value outside its shape, or the keys a mapping holds that it does not know.

    `unknown` lists the stray keys of the mapping at `path`, empty for a bad value.
    """

    path: tuple[str, ...]
    expected: str = ""
    found: str = ""
    unknown: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()


def shown(value: object) -> str:
    """Return how a report names a misfit value, a scalar by its repr."""
    if value is None or isinstance(value, int | float | str):
        return repr(value)
    return f"a {type(value).__name__}"


def alternatives(values: Iterable[object]) -> str:
    """Return the values a key accepts as a report names them, the last after `or`."""
    named = [
        repr(str(value)) if isinstance(value, str) else repr(value) for value in values
    ]
    return f"{', '.join(named[:-1])} or {named[-1]}"


def owned(section: Section, parent: Mapping[str, object]) -> bool:
    """Whether the keys of `section` are known, as no foreign backend owns them."""
    return section.default is None or parent.get("BACKEND", section.default) == (
        section.default
    )


def _bound(value: object, shape: Shape | Section) -> int | None:
    """Return `value` when it is a usable integer of `shape`, else `None`."""
    if isinstance(shape, Section) or not shape.usable(value):
        return None
    return value if isinstance(value, int) else None


def _out_of_order(
    scope: Mapping[str, object],
    section: Section,
    defaults: Mapping[str, object],
    path: tuple[str, ...],
) -> list[Misfit]:
    """Return a misfit for each integer at or above the key it must stay under."""
    found: list[Misfit] = []
    for low, high in section.below:
        value = _bound(scope.get(low), section.keys[low])
        ceiling = _bound(scope.get(high, defaults.get(high)), section.keys[high])
        if value is not None and ceiling is not None and value >= ceiling:
            expected = f"an integer below {high}, which is {ceiling}, or None"
            found.append(Misfit((*path, low), expected, shown(value)))
    return found


def _walk(
    scope: Mapping[str, object],
    section: Section,
    defaults: Mapping[str, object],
    path: tuple[str, ...],
    *,
    known: bool,
) -> list[Misfit]:
    """Return the misfits of one mapping level and of every section below it.

    A level a foreign backend owns may hold keys of its own, so only `known` names them.
    """
    found: list[Misfit] = []
    unknown = tuple(sorted(str(key) for key in scope if key not in section.keys))
    if unknown and known:
        found.append(Misfit(path, unknown=unknown, allowed=tuple(sorted(section.keys))))
    for key, shape in section.keys.items():
        if key not in scope:
            continue
        value = scope[key]
        where = (*path, key)
        if isinstance(shape, Shape):
            if not shape.usable(value):
                found.append(Misfit(where, shape.expected, shown(value)))
        elif not isinstance(value, Mapping):
            other = shape.other
            if other is None:
                found.append(Misfit(where, "a dict", shown(value)))
            elif not other.usable(value):
                found.append(Misfit(where, other.expected, shown(value)))
        else:
            nested = defaults.get(key)
            below = nested if isinstance(nested, Mapping) else EMPTY
            mine = known and owned(shape, scope)
            found.extend(_walk(value, shape, below, where, known=mine))
    found.extend(_out_of_order(scope, section, defaults, path))
    return found


def misfits(
    scope: Mapping[str, object],
    section: Section,
    defaults: Mapping[str, object] = EMPTY,
) -> list[Misfit]:
    """Return every value of `scope` outside its shape and every key it does not know.

    A section a foreign backend owns keeps its own keys, and `defaults` fill a ceiling.
    """
    return _walk(scope, section, defaults, (), known=True)


def misfit_text(prefix: str, misfit: Misfit) -> str:
    """Return how a report names one misfit value below `prefix`, and what it wants."""
    where = prefix + "".join(f"[{key!r}]" for key in misfit.path)
    return f"{where} must be {misfit.expected}, got {misfit.found}."


def enforce(
    prefix: str,
    scope: Mapping[str, object],
    section: Section,
    defaults: Mapping[str, object] = EMPTY,
) -> None:
    """Raise `SettingShapeError` naming every value of `scope` outside its shape.

    A server runs no system checks, so the first read refuses what a check would report.
    """
    found = [
        misfit_text(prefix, misfit)
        for misfit in misfits(scope, section, defaults)
        if not misfit.unknown
    ]
    if found:
        raise SettingShapeError(found)


def pick[T](
    scope: Mapping[str, object],
    key: str,
    usable: Callable[[object], TypeGuard[T]],
    defaults: Mapping[str, Any],
) -> T:
    """Return the value of `key` when `usable` passes it, its default otherwise."""
    value = scope.get(key)
    if key in scope and usable(value):
        return value
    default: T = defaults[key]
    return default


def flag(value: object) -> TypeGuard[bool]:
    """Whether `value` is `True` or `False`."""
    return isinstance(value, bool)


def text(value: object) -> TypeGuard[str]:
    """Whether `value` is a string that is not empty."""
    return isinstance(value, str) and bool(value)


def optional_text(value: object) -> TypeGuard[str | None]:
    """Whether `value` is `None` or a string that is not empty."""
    return value is None or text(value)


def texts(value: object) -> TypeGuard[Sequence[str]]:
    """Whether `value` is a list or a tuple of strings that are not empty."""
    return isinstance(value, list | tuple) and all(map(text, value))


def count(least: int) -> Callable[[object], TypeGuard[int]]:
    """Return the predicate passing an integer from `least`, a bool not counting."""

    def usable(value: object) -> TypeGuard[int]:
        return isinstance(value, int) and not isinstance(value, bool) and value >= least

    return usable


def number(*, positive: bool) -> Callable[[object], TypeGuard[float]]:
    """Return the predicate passing a finite number, above 0 or from 0, never a bool."""

    def usable(value: object) -> TypeGuard[float]:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        return math.isfinite(value) and (value > 0 if positive else value >= 0)

    return usable


def choice(values: tuple[object, ...]) -> Shape:
    """Return the shape of a key taking one of `values`, each matched with its type too.

    `True` equals `1`, so a value of another type never passes for one of `values`.
    """

    def fits(value: object, item: object) -> bool:
        if isinstance(item, str):
            return isinstance(value, str) and value == item
        return type(value) is type(item) and value == item

    def usable(value: object) -> bool:
        return any(fits(value, item) for item in values)

    return Shape(usable, alternatives(values))


def member(kind: type[enum.StrEnum]) -> Callable[[object], TypeGuard[str]]:
    """Return the predicate passing a string that names a member of `kind`."""
    names = frozenset(kind)

    def usable(value: object) -> TypeGuard[str]:
        return isinstance(value, str) and value in names

    return usable


def modes(kind: type[enum.StrEnum]) -> Shape:
    """Return the shape of a key that takes a member of `kind` by its string."""
    return Shape(member(kind), alternatives(kind))


__all__ = [
    "EMPTY",
    "Misfit",
    "Section",
    "Shape",
    "Usable",
    "alternatives",
    "choice",
    "count",
    "enforce",
    "flag",
    "member",
    "misfit_text",
    "misfits",
    "modes",
    "number",
    "optional_text",
    "owned",
    "pick",
    "shown",
    "text",
    "texts",
]
