"""Parameter-provider contracts and the auto-registered ABC.

`ParameterProvider` is the Protocol consumed by `DependencyResolver`.
`RegisteredParameterProvider` is the ABC used by the providers that ship with
the framework. Subclasses of the ABC join `provider_registry` through
`__init_subclass__`, which lets the resolver instantiate them without importing
them explicitly. Providers are consulted in ascending `priority` order.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol, override, runtime_checkable

from .registry import provider_registry


if TYPE_CHECKING:
    import inspect

    from .context import ResolutionContext
    from .resolver import DependencyResolver


@runtime_checkable
class ParameterProvider(Protocol):
    """Protocol consumed by `DependencyResolver`."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when this provider owns the parameter."""
        raise NotImplementedError

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the resolved value for the parameter."""
        raise NotImplementedError

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Classify the parameter from its signature alone, ahead of any context.

        True claims it in every context, False rules it out for good, and None
        leaves the verdict to `can_handle` at resolve time. The parameter
        carries the resolved type hint, falling back to the raw annotation.
        """
        raise NotImplementedError


class RegisteredParameterProvider(ABC):
    """Auto-registered base used by built-in providers shipped with the framework."""

    resolver: ClassVar[DependencyResolver]
    priority: ClassVar[int] = 100

    @override
    def __init_subclass__(cls, **kwargs) -> None:
        """Register the concrete subclass for lazy instantiation by the resolver."""
        super().__init_subclass__(**kwargs)
        provider_registry.add(cls)

    @abstractmethod
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when this provider owns the parameter."""

    @abstractmethod
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the resolved value for the parameter."""

    def static_can_handle(self, _param: inspect.Parameter) -> bool | None:
        """Leave every verdict to `can_handle`.

        A provider that can settle a parameter from the signature alone
        overrides this, so the plan compiler drops or claims it ahead of time.
        """
        return None
