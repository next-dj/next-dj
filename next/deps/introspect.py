"""Signature and annotation reads of a callable, memoised per callable.

Both resolvers and the plan compiler read the same facts off a callable, so the
memos and the parameter preparation they share sit below all three.
"""

from __future__ import annotations

import inspect
from types import MethodType
from typing import TYPE_CHECKING, Any, get_type_hints

from next.caches import LruCache


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from next.caches import BoundedCache


type IntrospectKey = tuple[object, bool]

# Everything an annotation expression is allowed to fail with. A string hint
# raises from the parser as readily as from the lookup, and under deferred
# evaluation the expression itself runs at this point.
HINT_ERRORS: tuple[type[Exception], ...] = (
    NameError,
    TypeError,
    AttributeError,
    ValueError,
    SyntaxError,
    KeyError,
    ImportError,
    RecursionError,
)

_EMPTY = inspect.Parameter.empty

# Bounded because the dev reloader re-executes a `page.py` on every save and mints
# fresh function objects, each of which would otherwise stay pinned here for good.
# Keyed by the stable `__func__` plus a bound flag, since a bound method is recreated.
_signature_cache: LruCache[IntrospectKey, inspect.Signature] = LruCache()
_type_hints_cache: LruCache[IntrospectKey, dict[str, Any]] = LruCache()
_var_keyword_cache: LruCache[IntrospectKey, bool] = LruCache()


def introspect_key(func: Callable[..., Any]) -> IntrospectKey:
    """Return the key the memos hold `func` under."""
    # `isinstance` over `inspect.ismethod`, which is a Python wrapper around the
    # same check and sits on every memoised lookup and plan replay.
    if isinstance(func, MethodType):
        return (func.__func__, True)
    return (func, False)


def _memoised[V](
    cache: BoundedCache[IntrospectKey, V],
    func: Callable[..., Any],
    compute: Callable[[Callable[..., Any]], V],
) -> V:
    """Return `compute(func)` through `cache`, computing it on a miss.

    A missing key raises rather than answering `None`, so a memo whose value may
    legitimately be `None` still tells a stored answer from an absent one.
    """
    key = introspect_key(func)
    try:
        return cache[key]
    except KeyError:
        pass
    except TypeError:
        # A callable no mapping can key is inspected afresh rather than refused.
        return compute(func)
    computed = compute(func)
    cache[key] = computed
    return computed


def cached_signature(func: Callable[..., Any]) -> inspect.Signature:
    """Return `inspect.signature(func)`, memoised per callable."""
    return _memoised(_signature_cache, func, inspect.signature)


def _hints_target(func: Callable[..., Any]) -> Callable[..., Any]:
    """Return the object whose annotations describe the parameters of `func`.

    Annotations on `func` itself belong to the class body and would shadow a parameter.
    """
    if inspect.isroutine(func):
        return func
    if inspect.isclass(func):
        if func.__init__ is not object.__init__:
            return func.__init__
        return func.__new__
    return type(func).__call__


def _resolve_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """Return the annotations of `func` with the `Annotated` extras kept."""
    return get_type_hints(_hints_target(func), include_extras=True)


def cached_type_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """Return the type hints of `func` with the `Annotated` extras kept, memoised.

    The hints come from whatever `inspect.signature` reads, so the plan never
    pairs one object's parameters with another object's annotations.
    """
    return _memoised(_type_hints_cache, func, _resolve_hints)


def _accepts_var_keyword(func: Callable[..., Any]) -> bool:
    """Return whether the signature of `func` ends in a `**kwargs` parameter."""
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in cached_signature(func).parameters.values()
    )


def cached_accepts_var_keyword(func: Callable[..., Any]) -> bool:
    """Return whether `func` declares a `**kwargs` parameter, memoised per callable."""
    return _memoised(_var_keyword_cache, func, _accepts_var_keyword)


def prepared_parameter(
    name: str, raw: inspect.Parameter, hints: Mapping[str, Any]
) -> tuple[inspect.Parameter, object]:
    """Return `raw` carrying its resolved hint, with the fallback its default gives.

    Shared by both resolvers so their outputs differ only by provider choice, not shape.
    """
    annotation = hints.get(name, raw.annotation)
    param = raw if annotation is raw.annotation else raw.replace(annotation=annotation)
    return param, None if param.default is _EMPTY else param.default


def forget_introspection_caches() -> None:
    """Drop every memo keyed by a callable, so a reloaded module leaves none behind."""
    _signature_cache.clear()
    _type_hints_cache.clear()
    _var_keyword_cache.clear()


__all__ = [
    "HINT_ERRORS",
    "IntrospectKey",
    "cached_accepts_var_keyword",
    "cached_signature",
    "cached_type_hints",
    "forget_introspection_caches",
    "introspect_key",
    "prepared_parameter",
]
