"""Parameter-provider contracts and the auto-registered ABC.

The ABC registers its subclasses, so the resolver never imports a provider by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, override, runtime_checkable

from .registry import provider_registry


if TYPE_CHECKING:
    import inspect

    from .context import ResolutionContext
    from .plan import AsyncParameterFiller, ParameterFiller, Reach
    from .resolver import DependencyResolver


@dataclass(frozen=True, slots=True)
class ContextReads:
    """What filling one parameter reads of the page context, which orders the steps.

    `keys` the parameter names itself, `nested` it reads under no name of its own.
    """

    keys: frozenset[str] = frozenset()
    nested: frozenset[str] = frozenset()
    dependencies: tuple[str, ...] = ()
    calls: tuple[Reach, ...] = ()
    every: bool = False


READS_NOTHING: Final = ContextReads()
"""The reads of a parameter filled without the context, as from the request."""

READS_EVERY: Final = ContextReads(every=True)
"""The reads of a parameter whose provider may read any key, which runs it last."""


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


@runtime_checkable
class ReachingParameterProvider(ParameterProvider, Protocol):
    """Protocol of a provider that tells the plan compiler what filling it calls.

    A provider without the hook still resolves correctly, its keys computed in segment.
    """

    def static_reach(self, param: inspect.Parameter) -> tuple[Reach, ...]:
        """Return each callable filling `param` calls, with its memo key or None.

        A shared key it names runs once per render through the request scope.
        """


@runtime_checkable
class AnsweringParameterProvider(ParameterProvider, Protocol):
    """Protocol of a provider whose filling answers a status by contract.

    A provider without the hook never makes the callable it fills run before a shell.
    """

    def static_answers(self, param: inspect.Parameter) -> bool:
        """Return whether filling `param` may raise `Http404` or `PermissionDenied`."""


@runtime_checkable
class ReadingParameterProvider(ParameterProvider, Protocol):
    """Protocol of a provider that tells the context graph what filling it reads.

    A provider without the hook may read any key, so its callable runs after the rest.
    """

    def static_reads(self, param: inspect.Parameter) -> ContextReads:
        """Return the context keys, dependencies and callables filling `param` reads."""


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

    def static_reach(self, _param: inspect.Parameter) -> tuple[Reach, ...]:
        """Report that filling the parameter calls no dependency.

        A provider resolving a dependency or a callable overrides this to name it.
        """
        return ()

    def static_reads(self, _param: inspect.Parameter) -> ContextReads:
        """Report that filling the parameter may read any key of the context.

        A provider reading named keys, or none, overrides this to say which.
        """
        return READS_EVERY

    def static_answers(self, _param: inspect.Parameter) -> bool:
        """Report that filling the parameter answers no status by contract.

        A provider that raises `Http404` or `PermissionDenied` by design overrides this.
        """
        return False
