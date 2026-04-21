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
from typing import TYPE_CHECKING, Any

from next.deps import DependencyResolver, RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect


_CONTEXT_DEFAULT_UNSET: object = object()


@dataclass(frozen=True, slots=True)
class Context:
    """Mark a parameter default so the value is taken from context_data.

    An empty `Context()` reads the parameter name from context_data. A
    string source reads that context key. A callable source is called
    with DI-resolved arguments. Any other object becomes a constant.
    The `default` keyword supplies a fallback when the context key is
    missing.
    """

    source: object | None = None
    default: object = field(default=_CONTEXT_DEFAULT_UNSET, kw_only=True)


@dataclass(frozen=True, slots=True)
class ContextResult:
    """Hold the full template context and its JavaScript-serializable subset.

    `context_data` contains every value merged into the Django template
    context. `js_context` contains only the subset marked
    `serialize=True`, which the renderer later hands to
    `StaticCollector.add_js_context`.
    """

    context_data: dict[str, Any]
    js_context: dict[str, Any]


class ContextByDefaultProvider(RegisteredParameterProvider):
    """Resolve parameters whose default value is a `Context` instance."""

    def __init__(self, resolver: DependencyResolver) -> None:
        """Store the dependency resolver used for callable context sources."""
        self._resolver = resolver

    def can_handle(self, param: inspect.Parameter, _context: object) -> bool:
        """Return True when the parameter default is a `Context` instance."""
        return isinstance(param.default, Context)

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Resolve the value from context_data, a callable, or a constant."""
        marker = param.default
        if not isinstance(marker, Context):
            return None

        source = marker.source
        context_data = getattr(context, "context_data", {}) or {}
        default_value: object = (
            None if marker.default is _CONTEXT_DEFAULT_UNSET else marker.default
        )

        if source is None:
            return context_data.get(param.name, default_value)

        if isinstance(source, str):
            return context_data.get(source, default_value)

        if callable(source):
            inner_ctx: dict[str, object] = {
                "request": getattr(context, "request", None),
                "form": getattr(context, "form", None),
                **(getattr(context, "url_kwargs", {}) or {}),
                "_cache": getattr(context, "cache", None),
                "_stack": getattr(context, "stack", None),
                "_context_data": context_data,
            }
            resolved = self._resolver.resolve_dependencies(source, **inner_ctx)
            return source(**resolved)

        return source


class ContextByNameProvider(RegisteredParameterProvider):
    """Inject context_data values when the parameter name is already a key."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when context_data already contains this parameter name."""
        context_data = getattr(context, "context_data", {}) or {}
        return param.name in context_data

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Return the value stored under the parameter name in context_data."""
        context_data = getattr(context, "context_data", {}) or {}
        return context_data[param.name]
