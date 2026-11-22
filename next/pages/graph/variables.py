"""The context variables an isolated step wrote, carried back into the caller."""

from __future__ import annotations

import contextvars
from enum import Enum, auto
from typing import Any, Final, NamedTuple


class _Absent(Enum):
    """The value no context variable or storage attribute held before a step ran."""

    TOKEN = auto()


_ABSENT: Final = _Absent.TOKEN

_LOCAL_STORAGE: Final = "asgiref.local"


class _Patch(NamedTuple):
    """The attributes one callable set and deleted in an `asgiref.local` storage."""

    changed: dict[str, object]
    dropped: frozenset[str]


type Writes = dict[contextvars.ContextVar[Any], object]


def _patch(value: dict[str, object], before: object) -> _Patch:
    """Return the attributes `value` sets or drops against the storage `before`."""
    held: dict[str, object] = before if isinstance(before, dict) else {}
    return _Patch(
        {
            name: item
            for name, item in value.items()
            if held.get(name, _ABSENT) is not item
        },
        frozenset(name for name in held if name not in value),
    )


def scope_writes(scope: contextvars.Context, start: contextvars.Context) -> Writes:
    """Return the variables `scope` holds another value of than `start` does.

    An `asgiref.local` storage is diffed by attribute, as one write copies all of them.
    It is told by the `asgiref.local` name of its variable and the dict it holds.
    """
    writes: Writes = {}
    for variable, value in scope.items():
        before = start.get(variable, _ABSENT)
        if before is value:
            continue
        if variable.name == _LOCAL_STORAGE and isinstance(value, dict):
            patch = _patch(value, before)
            if patch.changed or patch.dropped:
                writes[variable] = patch
            continue
        writes[variable] = value
    return writes


def restore(writes: Writes) -> None:
    """Set each written variable in the current context, as asgiref restores one.

    A storage patch applies its attributes over the storage the caller holds now.
    """
    for variable, value in writes.items():
        if isinstance(value, _Patch):
            merged = {**variable.get({}), **value.changed}
            for name in value.dropped:
                merged.pop(name, None)
            variable.set(merged)
            continue
        current = variable.get(_ABSENT)
        if current is _ABSENT or current != value:
            variable.set(value)


__all__ = ["Writes", "restore", "scope_writes"]
