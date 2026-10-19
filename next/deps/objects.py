"""The `DObject` marker and the provider that loads its row through `visible_to`.

Every lookup passes the `scope` dependency to `visible_to`, so a hidden row answers 404.
"""

from __future__ import annotations

import re
import types
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    Literal,
    NamedTuple,
    Protocol,
    Union,
    cast,
    get_args,
    get_origin,
    override,
)

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Model
from django.http import Http404

from .cache import _CACHE_MISS, DependencyCache, render_dep_cache
from .context import ResolutionContext
from .errors import UnboundObjectError
from .markers import DDependencyBase, unwrap_annotated
from .providers import RegisteredParameterProvider
from .resolver import current_resolver


if TYPE_CHECKING:
    import inspect
    from collections.abc import Callable, Mapping

    from django.db.models import QuerySet
    from django.http import HttpRequest

    from .plan import ParameterFiller
    from .resolver import DependencyResolver


SCOPE_DEPENDENCY: Final[str] = "scope"
"""The named dependency whose value every `visible_to` call receives."""

_MODEL_AND_PARENT: Final[int] = 2

_UNRESOLVED_MARKER: Final[re.Pattern[str]] = re.compile(r"\bDObject\b")

HINT_ADVICE: Final[str] = (
    "Import every name the signature uses at module level, and keep `from __future__ "
    "import annotations` out of modules the resolver inspects."
)
"""The fix an unresolved `DObject` hint calls for, shared by the provider and checks."""


class _ScopedQuery(Protocol):
    """The `visible_to` hook a `DObject` model carries on its default manager."""

    def __call__(self, scope: object, /) -> QuerySet[Any]:
        """Return the rows `scope` may see."""
        ...


class DObject[M](DDependencyBase[M]):
    """Annotation for the model row whose pk the URL kwarg of the parameter name holds.

    The optional parent names the context value, and field, the row must belong to.
    """

    __slots__ = ()

    def __class_getitem__(cls, item: object) -> object:
        """Build the marker, refusing at the annotation a shape no lookup could serve.

        The parent travels as a `Literal`, so `get_type_hints` keeps it a string.
        """
        args = item if isinstance(item, tuple) else (item,)
        model, parent = _validated(args)
        if parent is None:
            return types.GenericAlias(cls, (model,))
        return types.GenericAlias(cls, (model, Literal[parent]))


def _validated(args: tuple[object, ...]) -> tuple[type[Model], str | None]:
    """Return the model and parent name of a `DObject` subscript, or raise TypeError.

    `typing.get_type_hints` subscripts again with the parent already a `Literal`.
    """
    if not 1 <= len(args) <= _MODEL_AND_PARENT:
        msg = (
            "DObject takes a model and an optional parent name, as in DObject[Post] "
            f'or DObject[Comment, "post"], got {len(args)} arguments.'
        )
        raise TypeError(msg)
    model = args[0]
    if not isinstance(model, type) or not issubclass(model, Model):
        msg = f"DObject takes a model class first, got {model!r}."
        raise TypeError(msg)
    if model._meta.abstract:
        msg = f"DObject takes a concrete model, and {model.__name__} is abstract."
        raise TypeError(msg)
    parent = args[1] if len(args) == _MODEL_AND_PARENT else None
    if get_origin(parent) is Literal:
        parent = get_args(parent)[0]
    if parent is not None and not (isinstance(parent, str) and parent.isidentifier()):
        msg = (
            "DObject takes the parent as the name of a context value and a field "
            f"of {model.__name__}, got {parent!r}."
        )
        raise TypeError(msg)
    return model, parent


def object_target(annotation: object) -> tuple[type[Model], str | None] | None:
    """Return the model and parent a `DObject` annotation names, or None for others."""
    marker = unwrap_annotated(annotation)
    if get_origin(marker) is not DObject:
        return None
    args = get_args(marker)
    parent = get_args(args[1])[0] if len(args) == _MODEL_AND_PARENT else None
    return cast("type[Model]", args[0]), parent


def names_object(annotation: object) -> bool:
    """Whether an annotation names `DObject` anywhere, testing the common shapes first.

    A claimed union is refused at compile time rather than filled with the raw kwarg.
    """
    if isinstance(annotation, type):
        return annotation is DObject
    if isinstance(annotation, types.GenericAlias):
        return annotation.__origin__ is DObject
    if isinstance(annotation, str):
        return unresolved_object(annotation)
    return _names_object_in_form(annotation)


def _names_object_in_form(annotation: object) -> bool:
    """Whether a union or an `Annotated` form names `DObject` among its arguments."""
    if isinstance(annotation, types.UnionType):
        return any(map(names_object, annotation.__args__))
    origin = get_origin(annotation)
    if origin is Union:
        return any(map(names_object, get_args(annotation)))
    return origin is Annotated and names_object(get_args(annotation)[0])


def unresolved_object(annotation: object) -> bool:
    """Whether an annotation left as a string names the `DObject` marker.

    A string means the hints failed to resolve, so its parameter must fail loudly.
    """
    return (
        isinstance(annotation, str)
        and _UNRESOLVED_MARKER.search(annotation) is not None
    )


def scoped_query(model: type[Model]) -> _ScopedQuery | None:
    """Return the `visible_to` hook the default manager of `model` defines, if any."""
    hook = getattr(model._default_manager, "visible_to", None)
    return cast("_ScopedQuery", hook) if callable(hook) else None


def unscoped_reason(model: type[Model]) -> str:
    """Return why the rows of `model` cannot load, its manager lacking `visible_to`."""
    return (
        f"{model._meta.label}._default_manager.visible_to(scope), which that manager "
        "does not define. Add visible_to(scope) to the default manager, returning "
        "the rows the scope may see."
    )


def _scoped_query(model: type[Model]) -> _ScopedQuery:
    """Return `visible_to` off the default manager, or raise a TypeError naming it."""
    hook = scoped_query(model)
    if hook is None:
        msg = f"DObject[{model._meta.label}] reads through {unscoped_reason(model)}"
        raise TypeError(msg)
    return hook


def _owner(context: ResolutionContext, name: str, parent: str) -> object:
    """Return the parent value the context holds, raising when it holds no such key."""
    try:
        return context.context_data[parent]
    except KeyError:
        raise UnboundObjectError(name, parent, parent=True) from None


class _Rows(NamedTuple):
    """The `visible_to` hook of one model with the names its lookups go under."""

    visible_to: _ScopedQuery
    prefix: str
    not_found: str


_rows_of: dict[type[Model], _Rows] = {}


def _rows(model: type[Model]) -> _Rows:
    """Return the `visible_to` hook of `model` and its lookup names, read once a model.

    A router reload drops them along with the compiled plans that hold them too.
    """
    held = _rows_of.get(model)
    if held is None:
        held = _rows_of[model] = _Rows(
            _scoped_query(model),
            f"DObject[{model._meta.label_lower}]",
            f"No {model._meta.object_name} matches the given query.",
        )
    return held


def forget_object_rows(**kwargs: object) -> None:
    """Drop the memoised `visible_to` hooks, so a reload reads every manager again."""
    _rows_of.clear()


def _primary_key(
    rows: _Rows, to_python: Callable[[object], object], raw: object
) -> object:
    """Return `raw` as a primary key through `to_python`, a 404 when it refuses."""
    try:
        return to_python(raw)
    except (ValidationError, ValueError, TypeError):
        raise Http404(rows.not_found) from None


def _row_key(rows: _Rows, pk: object) -> str:
    """Return the cache key of the row at `pk`, shared by `DObject` and `scoped_row`."""
    return f"{rows.prefix}({pk!r})"


def _visible_row(
    rows: _Rows,
    key: str,
    resolver: DependencyResolver,
    context: ResolutionContext,
    param: inspect.Parameter | None,
    *lookups: Mapping[str, object],
) -> Model:
    """Return the first row `scope` sees under `lookups`, cached under `key`, or 404.

    The first match stands in for `get`, since a joining `visible_to` repeats rows.
    """
    cache = context.cache
    cached = cache.get(key)
    if cached is not _CACHE_MISS:
        return cast("Model", cached)
    scope = resolver._resolve_callable_dependency(
        SCOPE_DEPENDENCY, context, param=param
    )
    query = rows.visible_to(scope)
    for lookup in lookups:
        query = query.filter(**lookup)
    found = next(iter(query.order_by()[:1]), None)
    if found is None:
        raise Http404(rows.not_found)
    cache.set(key, found)
    return cast("Model", found)


def _pk_row(
    rows: _Rows,
    to_python: Callable[[object], object],
    raw: object,
    resolver: DependencyResolver,
    context: ResolutionContext,
    param: inspect.Parameter | None,
) -> Model:
    """Return the row whose primary key `raw` holds, cached under the shared row key."""
    pk = _primary_key(rows, to_python, raw)
    return _visible_row(rows, _row_key(rows, pk), resolver, context, param, {"pk": pk})


def _object_filler(
    resolver: DependencyResolver,
    param: inspect.Parameter,
    model: type[Model],
    parent: str | None,
) -> ParameterFiller:
    """Return the call that loads the row of `model` for one `DObject` parameter."""
    name = param.name
    to_python: Callable[[object], object] = model._meta.pk.to_python
    rows = _rows(model)
    prefix = rows.prefix

    def fill(context: ResolutionContext) -> object:
        url_kwargs = context.url_kwargs
        try:
            raw = url_kwargs[name]
        except KeyError:
            raise UnboundObjectError(name, name, available=url_kwargs) from None
        if parent is None:
            return _pk_row(rows, to_python, raw, resolver, context, param)
        pk = _primary_key(rows, to_python, raw)
        owner = _owner(context, name, parent)
        if owner is None:
            raise Http404(rows.not_found)
        owner_pk = getattr(owner, "pk", owner)
        key = f"{prefix}({pk!r}, {parent}={owner_pk!r})"
        return _visible_row(
            rows, key, resolver, context, param, {parent: owner}, {"pk": pk}
        )

    return fill


def object_kwarg(model: type[Model]) -> str:
    """Return the URL kwarg an `obj=DObject[Model]` reads, the `model_name` of `model`.

    Django derives `model_name` from the class name, which the stubs type as a `str`.
    """
    return model.__name__.lower()


def url_row(
    model: type[Model],
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
) -> Model:
    """Return the row of `model` whose pk the URL kwarg named after the model holds.

    It shares the cached row of a `DObject` parameter of the same model and pk.
    """
    meta = model._meta
    kwarg = object_kwarg(model)
    try:
        raw = url_kwargs[kwarg]
    except KeyError:
        raise UnboundObjectError(
            meta.label, kwarg, available=url_kwargs, declared=True
        ) from None
    context = ResolutionContext(
        request=request,
        form=None,
        url_kwargs=url_kwargs,
        context_data={},
        cache=DependencyCache(dep_cache),
    )
    resolver = current_resolver()
    return _pk_row(_rows(model), meta.pk.to_python, raw, resolver, context, None)


def scoped_model(model: type[Model]) -> bool:
    """Whether rows of `model` load through `visible_to(scope)` in this project.

    Both halves are needed, a `visible_to` on the default manager and a `scope`.
    """
    return (
        scoped_query(model) is not None
        and current_resolver().get_dependency(SCOPE_DEPENDENCY) is not None
    )


def scoped_row(
    model: type[Model], lookup: Mapping[str, object], request: HttpRequest | None
) -> Model:
    """Return the row of `model` matching `lookup` that `scope` sees, or raise 404.

    A lookup by the primary key alone shares the cached row a `DObject` loads.
    """
    if request is None:
        msg = (
            f"{model._meta.label} loads through visible_to(scope), and the scope "
            "resolves from the request, which get_initial() was not given. Pass it "
            "on from an override, as super().get_initial(request, **url_kwargs)."
        )
        raise TypeError(msg)
    rows = _rows(model)
    meta = model._meta
    if len(lookup) == 1 and next(iter(lookup)) in {"pk", meta.pk.name}:
        pk = _primary_key(rows, meta.pk.to_python, next(iter(lookup.values())))
        key = _row_key(rows, pk)
        lookup = {"pk": pk}
    else:
        pairs = ", ".join(f"{field}={value!r}" for field, value in lookup.items())
        key = f"{rows.prefix}({pairs})"
    context = ResolutionContext(
        request=request,
        form=None,
        url_kwargs={},
        context_data={},
        cache=DependencyCache(render_dep_cache(request)),
    )
    return _visible_row(rows, key, current_resolver(), context, None, lookup)


class ObjectProvider(RegisteredParameterProvider):
    """Fill `DObject[...]` parameters with the row the project scope may see.

    Priority 0 keeps a `Depends` default or a same-named value from going unscoped.
    """

    priority = 0

    def __init__(self, resolver: DependencyResolver) -> None:
        """Store the resolver the `scope` dependency is resolved through."""
        self._resolver = resolver

    @override
    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param)

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Claim every annotation naming `DObject`, unions too, so none goes raw."""
        return names_object(param.annotation)

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fill the parameter through the filler the plan compiler builds for it."""
        return self.compile_resolve(param)(context)

    @override
    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller:
        """Read the model, the parent and the `visible_to` hook once per plan."""
        annotation = param.annotation
        if isinstance(annotation, str):
            msg = (
                f'Parameter "{param.name}" keeps the annotation {annotation!r} as a '
                "string, because the type hints of its callable did not resolve. "
                f"{HINT_ADVICE}"
            )
            raise TypeError(msg)
        target = object_target(annotation)
        if target is None and unwrap_annotated(annotation) is DObject:
            msg = (
                f'Parameter "{param.name}" is annotated with a bare DObject. Name '
                "the model it loads, as in DObject[Post]."
            )
            raise TypeError(msg)
        if target is None:
            msg = (
                f'Parameter "{param.name}" is annotated {annotation!r}, which puts '
                "DObject in a union. A DObject is never optional, since a missing "
                "row answers 404, so annotate it with the DObject alone, as in "
                "DObject[Post]."
            )
            raise TypeError(msg)
        model, parent = target
        return _object_filler(self._resolver, param, model, parent)


def _unserved(param: inspect.Parameter) -> ImproperlyConfigured:
    """Return the error a `DObject` parameter no provider loads raises."""
    return ImproperlyConfigured(
        f'DObject parameter "{param.name}" reached no provider that loads its row, '
        "so it is refused rather than filled with the raw URL kwarg. Keep "
        "ObjectProvider in the resolver, or add a provider that claims DObject."
    )


class UnservedObjectProvider(RegisteredParameterProvider):
    """Refuse a `DObject` parameter that every earlier provider passed over.

    It runs last, so a resolver without `ObjectProvider` fails rather than go unscoped.
    """

    priority = 1000

    @override
    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param)

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Claim the annotations `ObjectProvider` claims."""
        return names_object(param.annotation)

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Raise the refusal of the parameter."""
        raise _unserved(param)

    @override
    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller:
        """Return the call that raises a fresh refusal on every resolve."""

        def fill(_context: ResolutionContext) -> object:
            raise _unserved(param)

        return fill


__all__ = ["DObject", "ObjectProvider"]
