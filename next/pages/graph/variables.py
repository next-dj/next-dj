"""The context variables an isolated step wrote, carried back into the caller."""

from __future__ import annotations

import contextvars
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Final, Protocol

from django.conf import settings
from django.urls import get_script_prefix, get_urlconf, set_script_prefix, set_urlconf
from django.utils import translation


if TYPE_CHECKING:
    from collections.abc import Iterable


class _Absent(Enum):
    """The value no context variable held before a step ran."""

    TOKEN = auto()


_ABSENT: Final = _Absent.TOKEN


type Writes = dict[contextvars.ContextVar[Any], object]

type Values = tuple[object, ...]


class LocalAccessor[T](Protocol):
    """Reads and sets one value that the steps of an overlapped layer may change."""

    def read(self) -> T:
        """Return the value the current context holds."""

    def write(self, value: T) -> None:
        """Set `value` in the current context."""


class _Language:
    """The active language, the default one being no override and `None` none."""

    __slots__ = ()

    def read(self) -> str | None:
        return translation.get_language()

    def write(self, value: str | None) -> None:
        if value is None:
            translation.deactivate_all()
        elif value == settings.LANGUAGE_CODE:
            translation.deactivate()
        else:
            translation.activate(value)


class _Urlconf:
    """The URLconf of the request."""

    __slots__ = ()

    def read(self) -> str | None:
        return get_urlconf()

    def write(self, value: str | None) -> None:
        set_urlconf(value)


class _ScriptPrefix:
    """The script prefix of the request."""

    __slots__ = ()

    def read(self) -> str:
        return get_script_prefix()

    def write(self, value: str) -> None:
        set_script_prefix(value)


class _Registry:
    """The accessors every overlapped layer reconciles, the Django ones first."""

    __slots__ = ("accessors",)

    def __init__(self, *accessors: LocalAccessor[Any]) -> None:
        """Start with `accessors`."""
        self.accessors = accessors


_registry = _Registry(_Language(), _Urlconf(), _ScriptPrefix())


def register_local_accessor(accessor: LocalAccessor[Any]) -> None:
    """Keep the value `accessor` reads at what the last overlapped step changed it to.

    A step that switches it and switches it back otherwise writes back the value it saw.
    """
    _registry.accessors = (*_registry.accessors, accessor)


def accessors() -> tuple[LocalAccessor[Any], ...]:
    """Return the accessors an overlapped layer reconciles, in registration order."""
    return _registry.accessors


def scope_writes(scope: contextvars.Context, start: contextvars.Context) -> Writes:
    """Return the variables `scope` holds another value of than `start` does."""
    return {
        variable: value
        for variable, value in scope.items()
        if start.get(variable, _ABSENT) is not value
    }


def restore(writes: Writes) -> None:
    """Set each written variable in the current context, as asgiref restores one."""
    for variable, value in writes.items():
        current = variable.get(_ABSENT)
        if current is _ABSENT or current != value:
            variable.set(value)


def read_values(accessors: tuple[LocalAccessor[Any], ...]) -> Values:
    """Return what the current context reads through each of `accessors`."""
    return tuple(accessor.read() for accessor in accessors)


def settle(
    accessors: tuple[LocalAccessor[Any], ...], start: Values, ends: Iterable[Values]
) -> None:
    """Set each of `accessors` to what the last step that changed it read at its end."""
    targets = list(start)
    for end in ends:
        for index, value in enumerate(end):
            if value != start[index]:
                targets[index] = value
    for accessor, target in zip(accessors, targets, strict=True):
        if accessor.read() != target:
            accessor.write(target)


__all__ = [
    "LocalAccessor",
    "Values",
    "Writes",
    "accessors",
    "read_values",
    "register_local_accessor",
    "restore",
    "scope_writes",
    "settle",
]
