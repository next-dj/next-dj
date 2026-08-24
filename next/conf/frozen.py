"""Immutable `list` and `dict` subclasses used for merged settings values.

Staying a `list` and a `dict` keeps every `isinstance` guard across the
framework working unchanged, so freezing the merge costs no sweep of the
readers.
"""

from __future__ import annotations

import copy
from typing import Any, Never, override

from django.core.exceptions import ImproperlyConfigured

from .defaults import USER_SETTING


_IMMUTABLE_MESSAGE = (
    f"Merged {USER_SETTING} settings are immutable. "
    f"Change settings.{USER_SETTING} and call next_framework_settings.reload(), "
    "or copy the value with list() or dict() to edit it."
)


def _immutable(*args: object, **kwargs: object) -> Never:
    """Reject every mutating call on a frozen settings container."""
    raise TypeError(_IMMUTABLE_MESSAGE)


class FrozenList(list[Any]):
    """A `list` whose mutating methods raise instead of changing contents.

    `__init__` stays unguarded because the constructor needs it, so
    re-invoking it by hand still refills the instance. That escape hatch
    sits at the same tier as calling `list.append` unbound, which no
    Python-level subclass can close.
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
        """Rebuild through the constructor so copy and pickle skip mutators."""
        return (type(self), (list(self),))


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
        """Rebuild through the constructor so copy and pickle skip mutators."""
        return (type(self), (dict(self),))


_MISSING = object()
_IN_PROGRESS = object()

# Leaves that cannot be mutated, so the merge may hand them out as they are.
_ATOMIC = (str, bytes, int, float, complex, frozenset, type(None))


def _freeze(value: object, memo: dict[int, object]) -> object:
    if isinstance(value, _ATOMIC):
        return value
    # Exact types only. A `defaultdict` rebuilt as a plain frozen mapping would
    # lose its factory, and a namedtuple its field names, so subclasses go
    # through `deepcopy` the way the merge handled every value before.
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

    Only exact `list`, `dict` and `tuple` values are rebuilt. Everything else,
    subclasses included, is deep-copied the way the merge handled it before, so
    the merge never aliases a value the caller still holds. The memo keeps
    shared subtrees shared and turns a self-referential config into
    `ImproperlyConfigured` rather than a `RecursionError`.
    """
    return _freeze(value, {})


__all__ = ["FrozenDict", "FrozenList", "freeze"]
