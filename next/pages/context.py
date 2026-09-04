"""Context annotation marker and providers that feed `context_data` into DI.

`Context` is the default-value marker used on page and layout parameters
to request a value from context_data. `ContextByDefaultProvider` handles
parameters whose default is a `Context` instance. `ContextByNameProvider`
injects context values when the parameter name already exists as a
context key. `ContextResult` packages the full context and the
JavaScript-serializable subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from next.deps import RESERVED_KEYS, DependencyResolver, RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect
    from collections.abc import Mapping

    from next.deps import ResolutionContext
    from next.static.serializers import JsContextSerializer


_CONTEXT_DEFAULT_UNSET: object = object()


def _from_context_data(
    context_data: Mapping[str, Any], key: str, default: object
) -> object:
    """Read `key` from context data, leaving the names dedicated providers own alone.

    A reserved key reaches the context data untouched, because nothing copies
    the mapping to strip it, so the marker stays blind to it here instead.
    """
    if key in RESERVED_KEYS:
        return default
    return context_data.get(key, default)


@dataclass(frozen=True, slots=True)
class Context:
    """Mark a parameter default so the value is taken from context_data.

    An empty `Context()` reads the parameter name from context_data. A
    string source reads that context key. A callable source is called
    with DI-resolved arguments. Any other object becomes a constant. The
    `default` keyword supplies a fallback when the context key is missing.
    """

    source: object | None = None
    default: object = field(default=_CONTEXT_DEFAULT_UNSET, kw_only=True)


@dataclass(frozen=True, slots=True)
class ContextResult:
    """Hold the full template context and its JavaScript-serializable subset.

    `context_data` contains every value merged into the Django template
    context. `js_context` contains only the subset marked
    `serialize=True`, which the renderer later hands to
    `StaticCollector.add_js_context`. `js_context_serializers` carries
    per-key serializer overrides supplied through
    `@context(serializer=...)` so the collector can route a single key
    through a custom serializer without affecting other keys.
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
        """Settle on the default alone. The marker never depends on the context."""
        return isinstance(param.default, Context)

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Resolve the value from context_data, a callable, or a constant."""
        marker = param.default
        if not isinstance(marker, Context):
            return None

        source = marker.source
        context_data = context.context_data
        default_value: object = (
            None if marker.default is _CONTEXT_DEFAULT_UNSET else marker.default
        )

        if source is None:
            return _from_context_data(context_data, param.name, default_value)

        if isinstance(source, str):
            return _from_context_data(context_data, source, default_value)

        if callable(source):
            resolved = self._resolver.resolve(source, context)
            return source(**resolved)

        return source


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
