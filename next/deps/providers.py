"""Parameter-provider contracts and the auto-registry ABC.

`ParameterProvider` is the minimal Protocol consumed by
`DependencyResolver`. `RegisteredParameterProvider` is the ABC used by
built-in providers that ship with the framework. Subclasses of the ABC
join the module-level `_registry` through `__init_subclass__`, which
lets the resolver instantiate them on first use without importing
them explicitly. The resolver consults providers in ascending
`priority` order, so a lower `priority` value is checked first.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol, override, runtime_checkable

from .signals import provider_registered


if TYPE_CHECKING:
    import inspect

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
    _registry: ClassVar[list[type[RegisteredParameterProvider]]] = []
    priority: ClassVar[int] = 100

    @override
    def __init_subclass__(cls, **kwargs: object) -> None:
        """Track concrete subclasses for lazy instantiation by the resolver."""
        super().__init_subclass__(**kwargs)
        RegisteredParameterProvider._registry.append(cls)
        provider_registered.send(sender=cls)

    @abstractmethod
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when this provider owns the parameter."""

    @abstractmethod
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the resolved value for the parameter."""
