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
from typing import (
    TYPE_CHECKING,
    Annotated,
    Literal,
    Union,
    get_args,
    get_origin,
    override,
)

from django.http import HttpRequest

from next.deps import DDependencyBase, RegisteredParameterProvider
from next.deps.markers import marker_origin, unwrap_annotated

from .parser import _coerce_url_value


if TYPE_CHECKING:
    from next.deps.context import ResolutionContext
    from next.deps.plan import ParameterFiller


class DUrl[T](DDependencyBase[T]):
    """Annotation for a captured URL path parameter with optional type coercion.

    The named forms carry the segment name, so a parameter keeps reading the
    same segment once either of the two is renamed away from the other.
    """

    __slots__ = ()

    def __class_getitem__(cls, item: object) -> object:
        """Build the marker for the type, named-key, and named-key-with-type forms.

        A plain type follows the standard generic path. A string, or a
        `(string, type)` tuple, is wrapped so the provider can read the
        captured segment by an explicit name rather than the parameter name.
        The name travels as a `Literal`, because `typing.get_type_hints`
        reads a bare string inside an alias as a forward reference and would
        resolve the segment name away into whatever global carries it.
        """
        if isinstance(item, (str, tuple)):
            raw = item if isinstance(item, tuple) else (item,)
            args = tuple(Literal[a] if isinstance(a, str) else a for a in raw)
            return types.GenericAlias(cls, args)
        return super().__class_getitem__(item)  # type: ignore[misc]


class DQuery[T](DDependencyBase[T]):
    """Annotation marker for a `request.GET` parameter.

    A query string carries neither a type nor an arity of its own, so the
    annotation supplies both and the list form takes the several shapes a
    front-end client may spell one repeated key in.
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
        """Return True when the request in context inhabits the annotated class.

        A handler asking for one concrete subclass under a server that serves
        another gets the parameter default rather than a request whose
        interface it would go on to call. The bare `HttpRequest` annotation
        asks for no subclass and takes whatever the context carries, which is
        what lets a test hand the handler a stand-in.
        """
        request = context.request
        if request is None:
            return False
        annotated = _request_annotation_class(param.annotation)
        if annotated is None:
            return False
        return annotated is HttpRequest or isinstance(request, annotated)

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
        key = _url_key(args) or param.name
        url_kwargs = context.url_kwargs
        raw = url_kwargs.get(key)
        if raw is None:
            return None
        return _coerce_url_value(raw, _url_type_hint(args))

    @override
    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller | None:
        """Read the segment name and the coercion type off the annotation once.

        The replay is left with a lookup in `url_kwargs` and the coercion itself.
        """
        args = _marker_args(param.annotation)
        key = _url_key(args) or param.name
        hint = _url_type_hint(args)

        def fill(context: ResolutionContext) -> object:
            raw = context.url_kwargs.get(key)
            if raw is None:
                return None
            return _coerce_url_value(raw, hint)

        return fill


def _marker_args(annotation: object) -> tuple[object, ...]:
    """Return the arguments of a marker annotation, past any `Annotated` wrapper."""
    return get_args(unwrap_annotated(annotation))


def _url_key(args: tuple[object, ...]) -> str | None:
    """Return the segment name a `DUrl` annotation carries, or `None` for no name.

    The name arrives wrapped, so the unwrapped argument is a string only for
    the named forms and the type of `DUrl[SomeType]` falls through.
    """
    first = args[0] if args else None
    if get_origin(first) is Literal:
        first = get_args(first)[0]
    return first if isinstance(first, str) else None


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
            else unwrap_annotated(annotation)
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

    One value is ambiguous between the three spellings, so the other two are
    read only once the plain repeated key has yielded no more than that.
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
    """Return values for `name` after considering the bracket and comma forms.

    `plain` carries at most one element by the time the caller gets here,
    which is what leaves room for the other two spellings to mean something.
    """
    only = plain[0] if plain else ""
    if not only:
        return request.GET.getlist(f"{name}[]")
    if "," in only:
        return [segment for segment in only.split(",") if segment]
    return plain


def get_multi_values(request: HttpRequest, name: str) -> list[str]:
    """Return every value `name` carries in `request.GET`.

    The three spellings a client may use for one repeated key are read in
    turn, so a caller sees one list whichever of them the front end sent.
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
