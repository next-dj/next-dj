"""Annotation markers and the default `Depends` provider.

`DDependencyBase` is the shared parent for type-annotation markers
such as `DForm` or `DUrl`. `Depends` is a dataclass default value used
to request dependency resolution by name, by callable, or by constant
injection. `DependsProvider` is the built-in parameter provider that
handles the `Depends` marker and registers itself through
`RegisteredParameterProvider`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, get_args, get_origin, override

from .providers import RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect

    from .context import ResolutionContext
    from .resolver import DependencyResolver


class DDependencyBase[T]:
    """Shared base for annotation markers such as `DForm` and `DUrl`."""

    __slots__ = ()


def marker_origin(annotation: object) -> object:
    """Return the marker an annotation names, seeing through one `Annotated` layer.

    The plan keeps the extras a resolved hint carries, so a provider matching
    on `DUrl` has to look past the metadata a caller wrapped it in.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return get_origin(get_args(annotation)[0])
    return origin


@dataclass(frozen=True, slots=True)
class Depends:
    """Mark a parameter as a dependency resolved by the resolver.

    Use as a default parameter value. `Depends("name")` resolves a
    registered callable by name. `Depends(callable)` calls a factory
    with DI-resolved arguments. `Depends(value)` injects a constant
    value directly. `Depends()` resolves by the parameter name.
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
            resolved = self._resolver.resolve(dep, context)
            return dep(**resolved)

        return dep
