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
import types
from typing import TYPE_CHECKING, Annotated, Union, get_args, get_origin, override

from django.http import HttpRequest

from next.deps import DDependencyBase, RegisteredParameterProvider
from next.deps.markers import marker_origin

from .parser import _coerce_url_value


if TYPE_CHECKING:
    from next.deps.context import ResolutionContext


class DUrl[T](DDependencyBase[T]):
    """Annotation for a captured URL path parameter with optional type coercion.

    Use `DUrl[SomeType]` to read the captured segment that matches the
    parameter name and coerce it. Use `DUrl["param"]` to read a named
    segment without coercion. Use `DUrl["param", SomeType]` to read a
    named segment and coerce it, which is the form to reach for when the
    parameter name differs from the captured segment name.
    """

    __slots__ = ()

    def __class_getitem__(cls, item: object) -> object:
        """Build the marker for the type, named-key, and named-key-with-type forms.

        A plain type follows the standard generic path. A string, or a
        `(string, type)` tuple, is wrapped so the provider can read the
        captured segment by an explicit name rather than the parameter name.
        """
        if isinstance(item, (str, tuple)):
            args = item if isinstance(item, tuple) else (item,)
            return types.GenericAlias(cls, args)
        return super().__class_getitem__(item)  # type: ignore[misc]


class DQuery[T](DDependencyBase[T]):
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


def _request_annotation_class(annotation: object) -> type | None:
    """Return the request class an annotation names, or `None` for every other shape.

    A union that mixes a request with another concrete type is refused because
    the provider has no way to choose between them. Both union origins are
    tested, since `Optional[HttpRequest]` spells `typing.Union` on 3.12 and
    3.13 while the PEP 604 form spells `types.UnionType`.
    """
    if annotation is HttpRequest:
        return HttpRequest
    if isinstance(annotation, type):
        return annotation if issubclass(annotation, HttpRequest) else None
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return _request_annotation_class(non_none[0])
    elif origin is Annotated:
        return _request_annotation_class(get_args(annotation)[0])
    return None


def _is_http_request_annotation(annotation: object) -> bool:
    """Return True for `HttpRequest`, a subclass, or a union of one with `None`."""
    return _request_annotation_class(annotation) is not None


class HttpRequestProvider(RegisteredParameterProvider):
    """Supply `HttpRequest` from `context.request`.

    The provider claims parameters annotated as `HttpRequest`, one of its
    subclasses, or the optional form of either. The optional form lets
    handlers keep `request: HttpRequest | None = None` for direct unit-test
    calls without giving up dependency injection.
    """

    priority = 50

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when the request in context is an instance of the annotation.

        A handler asking for one concrete subclass under a server that serves
        another gets the parameter default rather than a request whose
        interface it would go on to call.
        """
        request = context.request
        if request is None:
            return False
        annotated = _request_annotation_class(param.annotation)
        return annotated is not None and isinstance(request, annotated)

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out every other annotation. A match still needs a request present."""
        return None if _is_http_request_annotation(param.annotation) else False

    @override
    def resolve(self, _param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the request from the resolution context."""
        return context.request


class UrlByAnnotationProvider(RegisteredParameterProvider):
    """Fill `DUrl[...]` parameters from `url_kwargs`."""

    priority = 60

    @override
    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param) is True

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Settle on the annotation alone. The marker never depends on the context."""
        return marker_origin(param.annotation) is DUrl

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """URL value for the parameter, coerced when the annotation names a type."""
        args = _marker_args(param.annotation)
        key = args[0] if args and isinstance(args[0], str) else param.name
        url_kwargs = context.url_kwargs
        raw = url_kwargs.get(key)
        if raw is None:
            return None
        return _coerce_url_value(raw, _url_type_hint(args))


def _unwrap_annotated(annotation: object) -> object:
    """Return the type an `Annotated[...]` wraps, or the annotation unchanged."""
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def _marker_args(annotation: object) -> tuple[object, ...]:
    """Return the arguments of a marker annotation, past any `Annotated` wrapper."""
    return get_args(_unwrap_annotated(annotation))


def _url_type_hint(args: tuple[object, ...]) -> type:
    """Return the coercion type carried by a `DUrl` annotation, or `str`.

    `DUrl[SomeType]` carries the type at position 0. `DUrl["param", SomeType]`
    carries the key at position 0 and the type at position 1. Every other
    shape, including the bare `DUrl["param"]`, coerces to `str`.
    """
    if args and isinstance(args[0], type):
        return args[0]
    if len(args) > 1 and isinstance(args[1], type):
        return args[1]
    return str


class UrlKwargsProvider(RegisteredParameterProvider):
    """Fill parameters by name from `url_kwargs`."""

    priority = 70

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when `url_kwargs` contains this parameter name."""
        return param.name in context.url_kwargs

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Raw URL value for the parameter, coerced to the annotation when possible."""
        url_kwargs = context.url_kwargs
        raw = url_kwargs.get(param.name)
        if raw is None:
            return None
        annotation = param.annotation
        hint = (
            str
            if annotation is inspect.Parameter.empty
            else _unwrap_annotated(annotation)
        )
        return _coerce_url_value(raw, hint)


class QueryParamProvider(RegisteredParameterProvider):
    """Resolve `DQuery[...]` parameters from `request.GET`."""

    priority = 80

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True for `DQuery[...]` annotations when a request is present."""
        if marker_origin(param.annotation) is not DQuery:
            return False
        return context.request is not None

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out every other annotation. A match still needs a request present."""
        return None if marker_origin(param.annotation) is DQuery else False

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Pull the value from `request.GET` and coerce it to the annotated type."""
        request = context.request
        if request is None:
            return _missing(param)
        args = _marker_args(param.annotation)
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
    request: HttpRequest, param: inspect.Parameter, hint: object
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


def _expand_multi_value(request: HttpRequest, name: str, plain: list[str]) -> list[str]:
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


def get_multi_values(request: HttpRequest, name: str) -> list[str]:
    """Return all values for `name` from ``request.GET``.

    Tries three wire formats in order: plain repeated keys
    (``?brand=a&brand=b``), bracket suffix (``?brand[]=a&brand[]=b``),
    and comma-delimited (``?brand=a,b``). Returns an empty list when
    the parameter is absent in all three forms.
    """
    plain = request.GET.getlist(name)
    if len(plain) > 1:
        return plain
    return _expand_multi_value(request, name, plain)


__all__ = [
    "DQuery",
    "DUrl",
    "HttpRequestProvider",
    "QueryParamProvider",
    "UrlByAnnotationProvider",
    "UrlKwargsProvider",
    "get_multi_values",
]
