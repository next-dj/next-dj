"""Ordered, versioned registry of the provider classes that register themselves."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .signals import provider_registered


if TYPE_CHECKING:
    from collections.abc import Iterator

    from .providers import RegisteredParameterProvider


class ProviderRegistry:
    """Ordered list of provider classes with a version every resolver compares.

    A class registering under an address another class already holds replaces
    it in place, so the dev reloader re-executing the module that declares a
    provider leaves one entry rather than a growing pile of dead ones.
    """

    __slots__ = ("_ordered", "_version")

    def __init__(self) -> None:
        """Start empty at version zero."""
        self._ordered: list[type[RegisteredParameterProvider]] = []
        self._version: int = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every mutation."""
        return self._version

    def bump(self) -> None:
        """Move the version on, so every resolver rebuilds from the classes."""
        self._version += 1

    def add(self, cls: type[RegisteredParameterProvider]) -> None:
        """Register `cls`, replacing an earlier class of the same address."""
        address = (cls.__module__, cls.__qualname__)
        for index, existing in enumerate(self._ordered):
            if (existing.__module__, existing.__qualname__) == address:
                self._ordered[index] = cls
                break
        else:
            self._ordered.append(cls)
        self._version += 1
        provider_registered.send(sender=cls)

    def __iter__(self) -> Iterator[type[RegisteredParameterProvider]]:
        """Iterate over the classes in registration order."""
        return iter(self._ordered)

    def __len__(self) -> int:
        """Return how many classes have registered."""
        return len(self._ordered)


provider_registry: ProviderRegistry = ProviderRegistry()

__all__ = ["ProviderRegistry", "provider_registry"]
