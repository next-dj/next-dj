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
    Protocol,
    Union,
    cast,
    get_args,
    get_origin,
    override,
)

from django.core.exceptions import ValidationError
from django.db.models import Model
from django.http import Http404

from .cache import _CACHE_MISS
from .errors import UnboundObjectError
from .markers import DDependencyBase, unwrap_annotated
from .providers import RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect
    from collections.abc import Callable

    from django.db.models import QuerySet

    from .context import ResolutionContext
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


def _claims(annotation: object) -> bool:
    """Whether an annotation names `DObject` anywhere, testing the common shapes first.

    A claimed union is refused at compile time rather than filled with the raw kwarg.
    """
    if isinstance(annotation, type):
        return annotation is DObject
    if isinstance(annotation, types.GenericAlias):
        return annotation.__origin__ is DObject
    if isinstance(annotation, str):
        return unresolved_object(annotation)
    return _claims_form(annotation)


def _claims_form(annotation: object) -> bool:
    """Whether a union or an `Annotated` form names `DObject` among its arguments."""
    if isinstance(annotation, types.UnionType):
        return any(map(_claims, annotation.__args__))
    origin = get_origin(annotation)
    if origin is Union:
        return any(map(_claims, get_args(annotation)))
    return origin is Annotated and _claims(get_args(annotation)[0])


def unresolved_object(annotation: object) -> bool:
    """Whether an annotation left as a string names the `DObject` marker.

    A string means the hints failed to resolve, so its parameter must fail loudly.
    """
    return (
        isinstance(annotation, str)
        and _UNRESOLVED_MARKER.search(annotation) is not None
    )


def _scoped_query(model: type[Model]) -> _ScopedQuery:
    """Return `visible_to` off the default manager, or raise a TypeError naming it."""
    visible_to = getattr(model._default_manager, "visible_to", None)
    if not callable(visible_to):
        msg = (
            f"DObject[{model._meta.label}] reads through "
            f"{model._meta.label}._default_manager.visible_to(scope), which the "
            "manager does not define. Add it, returning the rows the scope may see."
        )
        raise TypeError(msg)
    return cast("_ScopedQuery", visible_to)


def _owner(context: ResolutionContext, name: str, parent: str) -> object:
    """Return the parent value the context holds, raising when it holds no such key."""
    try:
        return context.context_data[parent]
    except KeyError:
        raise UnboundObjectError(name, parent, parent=True) from None


def _object_filler(
    resolver: DependencyResolver,
    param: inspect.Parameter,
    model: type[Model],
    parent: str | None,
) -> ParameterFiller:
    """Return the call that loads the row of `model` for one `DObject` parameter.

    The first match stands in for `get`, since a joining `visible_to` repeats rows.
    """
    name = param.name
    to_python: Callable[[object], object] = model._meta.pk.to_python
    visible_to = _scoped_query(model)
    prefix = f"DObject[{model._meta.label_lower}]"
    not_found = f"No {model._meta.object_name} matches the given query."

    def fill(context: ResolutionContext) -> object:
        url_kwargs = context.url_kwargs
        try:
            raw = url_kwargs[name]
        except KeyError:
            raise UnboundObjectError(name, name, available=url_kwargs) from None
        try:
            pk = to_python(raw)
        except (ValidationError, ValueError, TypeError):
            raise Http404(not_found) from None
        if parent is None:
            owner = None
            key = f"{prefix}({pk!r})"
        else:
            owner = _owner(context, name, parent)
            if owner is None:
                raise Http404(not_found)
            owner_pk = getattr(owner, "pk", owner)
            key = f"{prefix}({pk!r}, {parent}={owner_pk!r})"
        cache = context.cache
        cached = cache.get(key)
        if cached is not _CACHE_MISS:
            return cached
        scope = resolver._resolve_callable_dependency(
            SCOPE_DEPENDENCY, context, param=param
        )
        rows = visible_to(scope)
        if parent is not None:
            rows = rows.filter(**{parent: owner})
        found = next(iter(rows.filter(pk=pk).order_by()[:1]), None)
        if found is None:
            raise Http404(not_found)
        cache.set(key, found)
        return found

    return fill


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
        return _claims(param.annotation)

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


__all__ = ["DObject", "ObjectProvider"]
