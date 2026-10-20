"""Annotation markers and the default `Depends` provider.

`Depends` ships with the provider that resolves it, so neither drifts from the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin, override

from .cache import _IN_PROGRESS
from .errors import DependencyCycleError
from .providers import RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect
    from collections.abc import Callable

    from .context import ResolutionContext
    from .plan import ParameterFiller
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
    """Return the dotted identity a callable `Depends` is tracked under.

    A factory has no registered name, so its address stands in on the stack.
    """
    qualname = getattr(factory, "__qualname__", "")
    if not qualname:
        return f"{type(factory).__qualname__}@{id(factory):x}"
    module = getattr(factory, "__module__", "") or ""
    return f"{module}.{qualname}"


def call_factory(
    resolver: DependencyResolver,
    factory: Callable[..., Any],
    key: str,
    context: ResolutionContext,
) -> object:
    """Call a factory `Depends` under the cycle guards the named form owns.

    Without them two factories naming each other raise `RecursionError`.
    """
    stack = context.stack
    if key in stack or context.cache.get(key) is _IN_PROGRESS:
        raise DependencyCycleError([*stack, key])
    stack.append(key)
    context.cache.mark_in_progress(key)
    try:
        return factory(**resolver.resolve(factory, context))
    finally:
        stack.pop()
        context.cache.unmark_in_progress(key)


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
            return self._resolver._resolve_callable_dependency(
                dep, context, param=param
            )

        if callable(dep):
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
                return resolver._resolve_callable_dependency(name, context, param=param)

            return by_name

        if callable(target):
            factory = target
            key = factory_key(factory)

            def by_callable(context: ResolutionContext) -> object:
                return call_factory(resolver, factory, key, context)

            return by_callable

        constant = target

        def by_constant(_context: ResolutionContext) -> object:
            return constant

        return by_constant
