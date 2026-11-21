"""Parameter-provider contracts and the auto-registered ABC.

The ABC registers its subclasses, so the resolver never imports a provider by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol, override, runtime_checkable

from .registry import provider_registry


if TYPE_CHECKING:
    import inspect

    from .context import ResolutionContext
    from .plan import AsyncParameterFiller, ParameterFiller
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

        True claims it for good, False rules it out, and None defers to `can_handle`.
        """
        raise NotImplementedError


@runtime_checkable
class CompilingParameterProvider(ParameterProvider, Protocol):
    """Protocol of a provider that also compiles the call filling its parameters.

    Split off `ParameterProvider`, as the hook is optional for existing providers.
    """

    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller | None:
        """Fold the work `resolve` repeats for `param` into one call the plan holds.

        Asked only for parameters `static_can_handle` claimed, once per plan.
        """


@runtime_checkable
class AsyncCompilingParameterProvider(ParameterProvider, Protocol):
    """Protocol of a provider that can hand the async replay a filler to await.

    Split off `ParameterProvider`, as the hook is optional for existing providers.
    """

    def compile_aresolve(self, param: inspect.Parameter) -> AsyncParameterFiller | None:
        """Return the call the async replay awaits for `param`, or None to stay sync.

        Asked only for parameters `static_can_handle` claimed, once per async plan.
        """


class RegisteredParameterProvider(ABC):
    """Auto-registered base used by built-in providers shipped with the framework.

    A provider that only reads the context in memory sets `blocking` to False.
    """

    resolver: ClassVar[DependencyResolver]
    priority: ClassVar[int] = 100
    blocking: ClassVar[bool] = True

    @override
    def __init_subclass__(cls, **kwargs: object) -> None:
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

        A provider settling a parameter from its signature overrides this.
        """
        return None

    def compile_resolve(self, _param: inspect.Parameter) -> ParameterFiller | None:
        """Leave every resolve to `resolve`.

        A provider claiming a parameter statically overrides this with a prebuilt call.
        """
        return None

    def static_blocking(self, _param: inspect.Parameter) -> bool:
        """Return whether filling the parameter may block, read off `blocking`.

        A provider whose fillers differ in cost per parameter overrides this.
        """
        return self.blocking

    def compile_aresolve(
        self, _param: inspect.Parameter
    ) -> AsyncParameterFiller | None:
        """Leave every parameter to the sync filler on the async path too.

        A provider with work to await overrides this with an async call.
        """
        return None
