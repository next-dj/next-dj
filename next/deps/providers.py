"""Parameter-provider contracts and the auto-registry ABC.

`ParameterProvider` is the minimal Protocol consumed by
`DependencyResolver`. `RegisteredParameterProvider` is the ABC used by
built-in providers that ship with the framework. Subclasses of the ABC
join the module-level `_registry` through `__init_subclass__`, which
lets the resolver instantiate them on first use without importing
them explicitly. `ProviderRegistry` is a lightweight list-style helper
kept around for tests and future external consumers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable


if TYPE_CHECKING:
    import inspect
    from collections.abc import Iterator

    from .context import ResolutionContext
    from .resolver import DependencyResolver


@runtime_checkable
class ParameterProvider(Protocol):
    """Minimal protocol consumed by `DependencyResolver`."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when this provider owns the parameter."""
        raise NotImplementedError

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the resolved value for the parameter."""
        raise NotImplementedError


class RegisteredParameterProvider(ABC):
    """Auto-registered base used by built-in providers shipped with the framework."""

    resolver: ClassVar[DependencyResolver]
    _registry: ClassVar[list[type]] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Track concrete subclasses for lazy instantiation by the resolver."""
        super().__init_subclass__(**kwargs)
        RegisteredParameterProvider._registry.append(cls)

    @abstractmethod
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when this provider owns the parameter."""

    @abstractmethod
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the resolved value for the parameter."""


class ProviderRegistry:
    """Explicit list-style registry for parameter providers."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._providers: list[ParameterProvider] = []

    def register(self, provider: ParameterProvider) -> None:
        """Append a provider to the registry."""
        self._providers.append(provider)

    def get_providers(self) -> tuple[ParameterProvider, ...]:
        """Return a frozen snapshot of registered providers."""
        return tuple(self._providers)

    def clear(self) -> None:
        """Drop every registered provider."""
        self._providers.clear()

    def __len__(self) -> int:
        """Return the number of registered providers."""
        return len(self._providers)

    def __iter__(self) -> Iterator[ParameterProvider]:
        """Iterate registered providers in registration order."""
        return iter(self._providers)
