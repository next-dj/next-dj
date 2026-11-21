"""Annotation markers and the default `Depends` provider.

`Depends` ships with the provider that resolves it, so neither drifts from the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin, override

from next.introspect import is_async_callable, is_coroutine_callable

from .cache import _CACHE_MISS, _IN_PROGRESS
from .errors import DependencyCycleError
from .introspect import cached_signature
from .providers import RegisteredParameterProvider
from .runner import bridge


if TYPE_CHECKING:
    import inspect
    from collections.abc import Callable

    from .context import ResolutionContext
    from .plan import AsyncParameterFiller, ParameterFiller
    from .resolver import DependencyResolver


class DDependencyBase[T]:
    """Shared base for annotation markers such as `DForm` and `DUrl`."""

    __slots__ = ()


def unwrap_annotated(annotation: object) -> object:
    """Return the type an `Annotated[...]` wraps, or the annotation unchanged.

    The plan keeps hint extras, so marker-matching providers must look past them.
    """
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def marker_origin(annotation: object) -> object:
    """Return the marker an annotation names, seeing through one `Annotated` layer.

    Not built on `unwrap_annotated`, as the plain annotation reads its origin once.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return get_origin(get_args(annotation)[0])
    return origin


def factory_key(factory: object) -> str:
    """Return the key a callable `Depends` is memoised and cycle-checked under.

    The address keeps apart factories sharing a path, as lambdas or two bound methods.
    """
    qualname = getattr(factory, "__qualname__", "")
    if not qualname:
        return f"{type(factory).__qualname__}@{id(factory):x}"
    module = getattr(factory, "__module__", "") or ""
    return f"{module}.{qualname}@{id(getattr(factory, '__self__', factory)):x}"


def call_factory(
    resolver: DependencyResolver,
    factory: Callable[..., Any],
    key: str,
    context: ResolutionContext,
) -> object:
    """Call a factory `Depends` once per cache, under the guards of the named form.

    A failure is not stored, so the next consumer calls the factory again.
    """
    cache = context.cache
    found = cache.get(key)
    if found is not _CACHE_MISS:
        if found is _IN_PROGRESS:
            raise DependencyCycleError([*context.stack, key])
        return found
    stack = context.stack
    if key in stack:
        raise DependencyCycleError([*stack[stack.index(key) :], key])
    stack.append(key)
    cache.mark_in_progress(key)
    try:
        value = factory(**resolver.resolve(factory, context))
    except BaseException:
        cache.unmark_in_progress(key)
        raise
    finally:
        stack.pop()
    cache.set(key, value)
    return value


def call_leaf(
    factory: Callable[[], object], key: str, context: ResolutionContext
) -> object:
    """Call a factory `Depends` with nothing to inject once per cache.

    Nothing to inject cannot re-enter, so it takes no stack or in-progress mark.
    """
    cache = context.cache
    found = cache.get(key)
    if found is not _CACHE_MISS:
        if found is _IN_PROGRESS:
            raise DependencyCycleError([*context.stack, key])
        return found
    value = factory()
    cache.set(key, value)
    return value


def injects_nothing(resolver: DependencyResolver, factory: Callable[..., Any]) -> bool:
    """Return whether the sync `factory` has no parameter the resolver would fill."""
    if is_async_callable(factory):
        return False
    try:
        parameters = cached_signature(factory).parameters.values()
    except (ValueError, TypeError):
        return True
    return all(resolver.skips(param) for param in parameters)


def bridge_factory(
    resolver: DependencyResolver,
    factory: Callable[..., Any],
    key: str,
    context: ResolutionContext,
) -> object:
    """Call an async factory `Depends` from the sync path, a finished one read back."""
    found = context.cache.get(key)
    if found is not _CACHE_MISS and found is not _IN_PROGRESS:
        return found
    return bridge(
        f"dependency {key}", lambda: resolver.acall_factory(factory, key, context)
    )


@dataclass(frozen=True, slots=True)
class Depends:
    """Mark a parameter as a dependency resolved by the resolver.

    The argument picks one of four forms: a name, a factory, a constant, or none.
    """

    dependency: object | None = None


class DependsProvider(RegisteredParameterProvider):
    """Provider that resolves parameters whose default is a `Depends` marker."""

    priority = 10

    def __init__(self, resolver: DependencyResolver) -> None:
        """Store the resolver used for nested dependency calls."""
        self._resolver = resolver

    @override
    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param) is True

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Settle on the default alone. The marker never depends on the context."""
        return isinstance(param.default, Depends)

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Resolve a `Depends` marker by name, callable, or constant."""
        marker = param.default
        if not isinstance(marker, Depends):
            return None

        dep = marker.dependency
        if dep is None:
            dep = param.name

        if isinstance(dep, str):
            return self._resolver.resolve_callable_dependency(dep, context, param=param)

        if callable(dep):
            if is_coroutine_callable(dep):
                return bridge_factory(self._resolver, dep, factory_key(dep), context)
            return call_factory(self._resolver, dep, factory_key(dep), context)

        return dep

    @override
    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller | None:
        """Settle which of the four forms the marker names, once per plan."""
        marker: Depends = param.default
        dep = marker.dependency
        target = param.name if dep is None else dep
        resolver = self._resolver

        if isinstance(target, str):
            name = target

            def by_name(context: ResolutionContext) -> object:
                return resolver.resolve_callable_dependency(name, context, param=param)

            return by_name

        if callable(target):
            factory = target
            key = factory_key(factory)
            if is_coroutine_callable(factory):

                def by_bridge(context: ResolutionContext) -> object:
                    return bridge_factory(resolver, factory, key, context)

                return by_bridge

            if injects_nothing(resolver, factory):

                def by_leaf(context: ResolutionContext) -> object:
                    return call_leaf(factory, key, context)

                return by_leaf

            def by_callable(context: ResolutionContext) -> object:
                return call_factory(resolver, factory, key, context)

            return by_callable

        constant = target

        def by_constant(_context: ResolutionContext) -> object:
            return constant

        return by_constant

    @override
    def static_blocking(self, param: inspect.Parameter) -> bool:
        """Block for a name or a factory running user code, not for a constant."""
        target = _marker_target(param)
        return isinstance(target, str) or callable(target)

    @override
    def compile_aresolve(self, param: inspect.Parameter) -> AsyncParameterFiller | None:
        """Await a name or a factory whose call reaches async code, once per plan.

        One reaching none stays a sync filler, so its whole call shares a segment.
        """
        target = _marker_target(param)
        resolver = self._resolver

        if isinstance(target, str):
            name = target
            if not resolver.awaits_dependency(name):
                return None

            async def by_name(context: ResolutionContext) -> object:
                return await resolver.aresolve_callable_dependency(
                    name, context, param=param
                )

            return by_name

        if callable(target):
            factory = target
            key = factory_key(factory)
            if not resolver.awaits(factory, key=key):
                return None

            async def by_callable(context: ResolutionContext) -> object:
                return await resolver.acall_factory(factory, key, context)

            return by_callable

        return None


def _marker_target(param: inspect.Parameter) -> object:
    """Return what the `Depends` default of `param` names, its own name when bare."""
    marker: Depends = param.default
    dep = marker.dependency
    return param.name if dep is None else dep
