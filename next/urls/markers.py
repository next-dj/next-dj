"""Dependency injection markers and providers for URL-derived parameters.

`DUrl` is an annotation marker used in `@context` and view-derived
callables to pull a value from URL kwargs. `DQuery` is the parallel
marker that reads `request.GET` query-string parameters. The provider
classes plug into the `next.deps` resolver via
`RegisteredParameterProvider` and expose `HttpRequest`, `DUrl[...]`
values, raw URL kwargs by name, and `DQuery[...]` values.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, TypeVar, get_args, get_origin, get_type_hints

from django.http import HttpRequest

from next.deps import DDependencyBase, RegisteredParameterProvider

from .parser import _coerce_url_value


if TYPE_CHECKING:
    from next.deps.context import ResolutionContext


logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class DUrl(DDependencyBase[_T]):
    """Annotation for a path or query parameter with optional type coercion.

    Use `DUrl["param"]` or `DUrl[SomeType]`.
    """

    __slots__ = ()


class DQuery(DDependencyBase[_T]):
    """Annotation marker for a `request.GET` parameter.

    Use `DQuery[str]`, `DQuery[int]`, `DQuery[bool]`, or `DQuery[float]`
    for scalar values, or `DQuery[list[T]]` for multi-value parameters.
    The list form accepts the plain repeated form `?brand=a&brand=b`,
    the qs-style bracket suffix `?brand[]=a&brand[]=b` emitted by axios
    and other front-end clients, and the comma-delimited form
    `?brand=a,b` produced by `qs.stringify` with the comma array
    format. The provider returns the parameter default when the key is
    absent, or `None` when no default is given.
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


class QueryParamProvider(RegisteredParameterProvider):
    """Resolve `DQuery[...]` parameters from `request.GET`."""

    def can_handle(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Return True for `DQuery[...]` annotations when a request is present."""
        if get_origin(param.annotation) is not DQuery:
            return False
        return getattr(context, "request", None) is not None

    def resolve(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> object:
        """Pull the value from `request.GET` and coerce it to the annotated type."""
        request = context.request
        if request is None:
            return _missing(param)
        args = get_args(param.annotation)
        hint = args[0] if args else str
        if get_origin(hint) is list:
            return _resolve_multi(request, param, hint)
        raw = request.GET.get(param.name)
        if raw is None:
            return _missing(param)
        return _coerce_url_value(raw, hint if isinstance(hint, type) else str)


def _missing(param: inspect.Parameter) -> object:
    """Return the param default or `None` when no key is present in `request.GET`."""
    return param.default if param.default is not inspect.Parameter.empty else None


def _resolve_multi(
    request: HttpRequest,
    param: inspect.Parameter,
    hint: object,
) -> object:
    """Resolve a `DQuery[list[T]]` parameter from repeated query-string keys.

    The function tries three wire formats in order. The plain repeated
    form `?brand=a&brand=b` wins first. The qs-style bracket suffix
    `?brand[]=a&brand[]=b` is the second fallback. The comma-delimited
    form `?brand=a,b` is the third fallback. When none of the three
    yield values, the parameter default is returned.
    """
    inner = get_args(hint)
    first = inner[0] if inner else str
    inner_type = first if isinstance(first, type) else str
    raw_list = request.GET.getlist(param.name)
    if len(raw_list) <= 1:
        raw_list = _expand_multi_value(request, param.name, raw_list)
    if not raw_list and param.default is not inspect.Parameter.empty:
        return param.default
    return [_coerce_url_value(v, inner_type) for v in raw_list]


def _expand_multi_value(
    request: HttpRequest,
    name: str,
    plain: list[str],
) -> list[str]:
    """Return values for `name` after considering bracket and comma forms.

    `plain` holds whatever `request.GET.getlist(name)` returned and is
    expected to have at most one element. An empty `plain` or a single
    empty string falls back to the bracket form `name[]`. A single
    non-empty string is split on commas when commas are present and
    empty segments are dropped. Otherwise `plain` is returned unchanged.
    """
    only = plain[0] if plain else ""
    if not only:
        return request.GET.getlist(f"{name}[]")
    if "," in only:
        return [segment for segment in only.split(",") if segment]
    return plain


__all__ = [
    "DQuery",
    "DUrl",
    "HttpRequestProvider",
    "QueryParamProvider",
    "UrlByAnnotationProvider",
    "UrlKwargsProvider",
]
