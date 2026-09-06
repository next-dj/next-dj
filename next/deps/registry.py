"""Ordered, versioned registry of the provider classes that register themselves."""

from __future__ import annotations

from types import CodeType
from typing import TYPE_CHECKING

from .signals import provider_registered


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .providers import RegisteredParameterProvider


type _Address = tuple[str | None, str, str]


def _body_file(cls: type) -> str | None:
    """Return the file the body of `cls` was compiled from, or `None` for none.

    The file router execs every `page.py` under one module name, so the module
    alone cannot tell two providers of the same class name apart. A method
    written in the class body still carries the path in its code object.
    """
    for value in vars(cls).values():
        code = getattr(value, "__code__", None)
        if isinstance(code, CodeType):
            return code.co_filename
    return None


def _address(cls: type) -> _Address:
    """Return the identity a re-executed declaration of `cls` registers under."""
    return (_body_file(cls), cls.__module__, cls.__qualname__)


class ProviderRegistry:
    """Ordered list of provider classes with a version every resolver compares.

    A class registering under an address another class already holds replaces
    it in place, so the dev reloader re-executing the module that declares a
    provider leaves one entry rather than a growing pile of dead ones.
    """

    __slots__ = ("_index", "_ordered", "_version")

    def __init__(self) -> None:
        """Start empty at version zero."""
        self._ordered: list[type[RegisteredParameterProvider]] = []
        self._index: dict[_Address, int] = {}
        self._version: int = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every mutation."""
        return self._version

    def add(self, cls: type[RegisteredParameterProvider]) -> None:
        """Register `cls`, replacing an earlier class of the same address."""
        address = _address(cls)
        index = self._index.get(address)
        if index is None:
            self._index[address] = len(self._ordered)
            self._ordered.append(cls)
        else:
            self._ordered[index] = cls
        self._version += 1
        provider_registered.send(sender=cls)

    def replace(self, classes: Iterable[type[RegisteredParameterProvider]]) -> None:
        """Put `classes` in place of the registered ones and move the version on.

        The counter goes forward even though the list goes back, so two states
        never share a version a cached plan would read as fresh.
        """
        self._ordered[:] = classes
        self._index = {_address(cls): i for i, cls in enumerate(self._ordered)}
        self._version += 1

    def __iter__(self) -> Iterator[type[RegisteredParameterProvider]]:
        """Iterate over the classes in registration order."""
        return iter(self._ordered)

    def __len__(self) -> int:
        """Return how many classes have registered."""
        return len(self._ordered)


provider_registry: ProviderRegistry = ProviderRegistry()

__all__ = ["ProviderRegistry", "provider_registry"]
