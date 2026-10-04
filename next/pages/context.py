"""The `Context` parameter marker and the providers that inject `context_data` values.

A value is injected through an explicit `Context` default, or by a parameter name that
matches an existing key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from next.deps import RESERVED_KEYS, DependencyResolver, RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect

    from next.deps import ResolutionContext
    from next.deps.plan import ParameterFiller
    from next.static.serializers import JsContextSerializer


_CONTEXT_DEFAULT_UNSET: object = object()


@dataclass(frozen=True, slots=True)
class Context:
    """Mark a parameter default so the value is taken from context_data.

    The source decides which form applies, and `default` covers a missing context key.
    """

    source: object | None = None
    default: object = field(default=_CONTEXT_DEFAULT_UNSET, kw_only=True)


@dataclass(frozen=True, slots=True)
class ContextResult:
    """Hold the full template context and its JavaScript-serializable subset.

    Only the keys of a context marked for serialization reach the client, each through
    its own serializer, so that subset is held separately.
    """

    context_data: dict[str, Any]
    js_context: dict[str, Any]
    js_context_serializers: dict[str, JsContextSerializer] = field(default_factory=dict)


class ContextByDefaultProvider(RegisteredParameterProvider):
    """Resolve parameters whose default value is a `Context` instance."""

    priority = 20

    def __init__(self, resolver: DependencyResolver) -> None:
        """Store the dependency resolver used for callable context sources."""
        self._resolver = resolver

    @override
    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param) is True

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Decide from the default alone, which no resolution context changes."""
        return isinstance(param.default, Context)

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fill the parameter through the same filler the plan compiler builds for it.

        Both paths share one decision tree, so a marker cannot resolve differently in a
        compiled plan and in a resolve without a plan.
        """
        if not isinstance(param.default, Context):
            return None
        return self.compile_resolve(param)(context)

    @override
    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller:
        """Read the source and the default of the marker once per plan.

        A reserved key never reaches the marker, so the parameter takes its default.
        """
        marker: Context = param.default
        source = marker.source
        resolver = self._resolver
        fallback: object = (
            None if marker.default is _CONTEXT_DEFAULT_UNSET else marker.default
        )

        if source is None or isinstance(source, str):
            key = param.name if source is None else source
            if key in RESERVED_KEYS:

                def by_default(_context: ResolutionContext) -> object:
                    return fallback

                return by_default

            def by_key(context: ResolutionContext) -> object:
                return context.context_data.get(key, fallback)

            return by_key

        if callable(source):
            factory = source

            def by_callable(context: ResolutionContext) -> object:
                return factory(**resolver.resolve(factory, context))

            return by_callable

        constant = source

        def by_constant(_context: ResolutionContext) -> object:
            return constant

        return by_constant


class ContextByNameProvider(RegisteredParameterProvider):
    """Inject context_data values when the parameter name is already a key."""

    priority = 30

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when context_data holds this name and no provider owns it."""
        name = param.name
        return name not in RESERVED_KEYS and name in context.context_data

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out the reserved names for good, so the plan drops this provider."""
        return False if param.name in RESERVED_KEYS else None

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the value stored under the parameter name in context_data."""
        return context.context_data[param.name]
