"""Dependency injection markers and providers for URL-derived parameters.

`DUrl` is an annotation marker used in `@context` and view-derived
callables to pull a value from URL kwargs. The three provider classes
plug into the `next.deps` resolver via `RegisteredParameterProvider`
and expose `HttpRequest`, `DUrl[...]` values, and raw URL kwargs by
name.
"""

from __future__ import annotations

import inspect
import logging
from typing import TypeVar, get_args, get_origin, get_type_hints

from django.http import HttpRequest

from next.deps import DDependencyBase, RegisteredParameterProvider

from .parser import _coerce_url_value


logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class DUrl(DDependencyBase[_T]):
    """Annotation for a path or query parameter with optional type coercion.

    Use `DUrl["param"]` or `DUrl[SomeType]`.
    """

    __slots__ = ()


class HttpRequestProvider(RegisteredParameterProvider):
    """Supply `HttpRequest` from `context.request`."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when the parameter is `HttpRequest` and a request exists."""
        if getattr(context, "request", None) is None:
            return False
        stack = getattr(self.resolver, "_resolve_call_stack", ())
        if stack:
            func = stack[-1]
            try:
                hints = get_type_hints(func)
                if hints.get(param.name) is HttpRequest:
                    return True
            except (NameError, TypeError, AttributeError, ValueError):
                logger.debug(
                    "Failed to resolve type hints for %r "
                    "when checking HttpRequest parameter %s",
                    func,
                    param.name,
                    exc_info=True,
                )
        origin = get_origin(param.annotation)
        return origin is None and param.annotation is HttpRequest

    def resolve(self, _param: inspect.Parameter, context: object) -> object:
        """Return the request from the resolution context."""
        return getattr(context, "request", None)


class UrlByAnnotationProvider(RegisteredParameterProvider):
    """Fill `DUrl[...]` parameters from `url_kwargs`."""

    def can_handle(self, param: inspect.Parameter, _context: object) -> bool:
        """Return True when the parameter uses a `DUrl` annotation."""
        return get_origin(param.annotation) is DUrl

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """URL value for the parameter, coerced when the annotation is a type."""
        args = get_args(param.annotation)
        key = args[0] if args and isinstance(args[0], str) else param.name
        url_kwargs = getattr(context, "url_kwargs", {}) or {}
        raw = (
            url_kwargs.get(key) if isinstance(key, str) else url_kwargs.get(param.name)
        )
        if raw is None:
            return None
        hint = args[0] if args and isinstance(args[0], type) else str
        return _coerce_url_value(str(raw), hint)


class UrlKwargsProvider(RegisteredParameterProvider):
    """Fill parameters by name from `url_kwargs`."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when `url_kwargs` contains this parameter name."""
        return param.name in (getattr(context, "url_kwargs", {}) or {})

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Raw URL value for the parameter, coerced to the annotation when possible."""
        url_kwargs = getattr(context, "url_kwargs", {}) or {}
        raw = url_kwargs.get(param.name)
        if raw is None:
            return None
        hint = (
            param.annotation if param.annotation is not inspect.Parameter.empty else str
        )
        if hint is str or hint is inspect.Parameter.empty:
            return str(raw)
        return _coerce_url_value(str(raw), hint)


__all__ = [
    "DUrl",
    "HttpRequestProvider",
    "UrlByAnnotationProvider",
    "UrlKwargsProvider",
]
