"""Immutable `list` and `dict` subclasses used for merged settings values.

Staying real builtins keeps every `isinstance` guard across the framework
working unchanged, and `copy.copy` and `copy.deepcopy` thaw a value back to
plain containers when a caller needs a mutable one.
"""

from __future__ import annotations

import copy
from typing import Any, Never, override

from django.core.exceptions import ImproperlyConfigured

from .defaults import USER_SETTING


_IMMUTABLE_MESSAGE = (
    f"Merged {USER_SETTING} settings are immutable. "
    f"Change settings.{USER_SETTING} and call next_framework_settings.reload(), "
    "or copy the value with list(), dict() or copy.deepcopy() to edit it."
)


def _immutable(*args: object, **kwargs: object) -> Never:
    """Reject every mutating call on a frozen settings container."""
    raise TypeError(_IMMUTABLE_MESSAGE)


class FrozenList(list[Any]):
    """A `list` whose mutating methods raise instead of changing contents.

    `__init__` stays unguarded, an escape hatch the constructor needs.
    """

    __slots__ = ()

    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        """Rebuild through the constructor so pickle skips the mutators."""
        return (type(self), (list(self),))

    def __copy__(self) -> list[Any]:
        """Return a plain mutable list, the same escape hatch as `list()`."""
        return list(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        """Return a plain mutable deep copy, thawed all the way down."""
        thawed: list[Any] = []
        memo[id(self)] = thawed
        thawed.extend(copy.deepcopy(item, memo) for item in self)
        return thawed


class FrozenDict(dict[Any, Any]):
    """A `dict` whose mutating methods raise instead of changing contents.

    Carries the same unguarded `__init__` escape hatch as `FrozenList`.
    """

    __slots__ = ()

    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        """Rebuild through the constructor so pickle skips the mutators."""
        return (type(self), (dict(self),))

    def __copy__(self) -> dict[Any, Any]:
        """Return a plain mutable dict, the same escape hatch as `dict()`."""
        return dict(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[Any, Any]:
        """Return a plain mutable deep copy, thawed all the way down."""
        thawed: dict[Any, Any] = {}
        memo[id(self)] = thawed
        for key, value in self.items():
            thawed[copy.deepcopy(key, memo)] = copy.deepcopy(value, memo)
        return thawed


_MISSING = object()
_IN_PROGRESS = object()

# Leaves that cannot be mutated, so the merge may hand them out as they are.
_ATOMIC = (str, bytes, int, float, complex, frozenset, type(None))


def _freeze(value: object, memo: dict[int, object]) -> object:
    if isinstance(value, _ATOMIC):
        return value
    # Exact types only, since a rebuilt `defaultdict` would lose its factory and
    # a rebuilt namedtuple its field names. Subclasses go through `deepcopy`.
    if type(value) is tuple:
        return tuple(_freeze(item, memo) for item in value)
    if type(value) is not dict and type(value) is not list:
        return copy.deepcopy(value)
    seen = memo.get(id(value), _MISSING)
    if seen is _IN_PROGRESS:
        msg = (
            f"settings.{USER_SETTING} contains a self-referential value, "
            "which cannot be frozen."
        )
        raise ImproperlyConfigured(msg)
    if seen is not _MISSING:
        return seen
    memo[id(value)] = _IN_PROGRESS
    frozen: object
    if isinstance(value, dict):
        frozen = FrozenDict({key: _freeze(item, memo) for key, item in value.items()})
    else:
        frozen = FrozenList([_freeze(item, memo) for item in value])
    memo[id(value)] = frozen
    return frozen


def freeze(value: object) -> object:
    """Return a deep immutable copy of one merged settings value.

    Exact `list`, `dict` and `tuple` values are rebuilt and everything else is
    deep-copied, so the merge aliases nothing the caller still holds. A
    self-referential config raises `ImproperlyConfigured`, not a `RecursionError`.
    """
    return _freeze(value, {})


__all__ = ["FrozenDict", "FrozenList", "freeze"]
