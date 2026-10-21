"""Form construction and hook invocation for the form action dispatch pipeline."""

import types
from typing import TYPE_CHECKING, Any, Final, cast

from django.db.models import Model
from django.forms.models import BaseModelForm as DjangoBaseModelForm

from next.conf.sentinels import UNSET
from next.deps import REQUEST_DEP_CACHE_ATTR
from next.deps.introspect import cached_accepts_var_keyword
from next.deps.resolver import current_resolver
from next.forms.origin import url_kwargs_for_request


if TYPE_CHECKING:
    from collections.abc import Callable

    from django import forms as django_forms
    from django.http import HttpRequest

    from next.forms.base import _FormKwargsHook


_FACTORY_TUPLE_LEN = 2
_DISPATCH_BOUND_KWARGS: Final[tuple[str, ...]] = ("data", "files")

type _DepsState = tuple[dict[str, Any], list[str]]


def _action_prefix(action_name: str | None) -> str:
    """Return the `Action 'name': ` lead of an error message, empty without a name."""
    return f"Action {action_name!r}: " if action_name else ""


def _initial_form_kwargs(form_class: type, initial_data: object) -> dict[str, Any]:
    """Map a `get_initial` result to the constructor kwargs of its form."""
    if isinstance(initial_data, Model):
        if not issubclass(form_class, DjangoBaseModelForm):
            msg = (
                f"get_initial for {form_class.__name__} returned a "
                f"{type(initial_data).__name__} instance, but "
                f"{form_class.__name__} is not a ModelForm. Subclass "
                "next.forms.ModelForm to bind model instances."
            )
            raise TypeError(msg)
        return {"instance": initial_data}
    return {"initial": initial_data}


def _construct_form(
    form_class: "type[django_forms.Form]",
    form_kwargs: dict[str, Any],
    *,
    request: "HttpRequest | None",
) -> "django_forms.Form":
    """Build a form from complete constructor kwargs, bound to POST for a request."""
    if request is None:
        return form_class(**form_kwargs)
    files = request.FILES if hasattr(request, "FILES") else None
    return form_class(data=request.POST, files=files, **form_kwargs)


def _accepts_var_keyword(func: "Callable[..., Any]") -> bool:
    """Return True when `func` declares a `**kwargs` parameter."""
    try:
        return cached_accepts_var_keyword(func)
    except (TypeError, ValueError):
        return False


def _resolve_and_call(
    hook: "Callable[..., Any]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    deps: _DepsState,
) -> object:
    """Resolve a hook's dependencies and call it, feeding url_kwargs to kwargs."""
    cache, stack = deps
    resolved = current_resolver().resolve_dependencies(
        hook, request=request, _cache=cache, _stack=stack, **url_kwargs
    )
    if _accepts_var_keyword(hook):
        for key, value in url_kwargs.items():
            resolved.setdefault(key, value)
    return hook(**resolved)


def _call_on_request_cache(
    hook: "Callable[..., Any]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    deps: _DepsState,
) -> object:
    """Call `hook` as `_resolve_and_call` does, its cache hung on `request` meanwhile.

    A request already carrying that cache keeps it, so dispatch hangs nothing twice.
    """
    cache = deps[0]
    previous = getattr(request, REQUEST_DEP_CACHE_ATTR, UNSET)
    if previous is cache:
        return _resolve_and_call(hook, request, url_kwargs, deps=deps)
    setattr(request, REQUEST_DEP_CACHE_ATTR, cache)
    try:
        return _resolve_and_call(hook, request, url_kwargs, deps=deps)
    finally:
        if previous is UNSET:
            delattr(request, REQUEST_DEP_CACHE_ATTR)
        else:
            setattr(request, REQUEST_DEP_CACHE_ATTR, previous)


def _call_get_initial(
    form_class: "type[django_forms.Form]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    deps: _DepsState,
    action_name: str | None = None,
) -> object:
    """Resolve `get_initial` dependencies and call it, feeding url_kwargs to kwargs.

    The caller's cache rides on the request, so `instance_from_url` shares its rows.
    """
    if not hasattr(form_class, "get_initial"):
        msg = (
            f"{_action_prefix(action_name)}{form_class.__name__} has no get_initial "
            "method. Subclass next.forms.Form or next.forms.ModelForm, or define a "
            "get_initial classmethod."
        )
        raise TypeError(msg)
    return _call_on_request_cache(
        form_class.get_initial, request, url_kwargs, deps=deps
    )


def _call_get_form_kwargs(
    form_class: "type[django_forms.Form]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    deps: _DepsState,
    action_name: str | None = None,
) -> dict[str, Any]:
    """Resolve an overridden `get_form_kwargs` and call it, insisting on a dict.

    The caller's cache rides on the request for the call alone, so `super()` shares it.
    """
    hook = cast("type[_FormKwargsHook]", form_class).get_form_kwargs
    raw = _call_on_request_cache(hook, request, url_kwargs, deps=deps)
    prefix = _action_prefix(action_name)
    if not isinstance(raw, dict):
        msg = (
            f"{prefix}{form_class.__name__}.get_form_kwargs returned "
            f"{type(raw).__name__}, expected a dict of form constructor kwargs. "
            "Return a dict, starting from super().get_form_kwargs(request) to "
            "keep the get_initial defaults."
        )
        raise TypeError(msg)
    for key in _DISPATCH_BOUND_KWARGS:
        if key in raw:
            msg = (
                f"{prefix}{form_class.__name__}.get_form_kwargs returned the "
                f"{key!r} key. Drop it, the dispatcher passes data and files to "
                "the bound form itself."
            )
            raise TypeError(msg)
    return cast("dict[str, Any]", raw)


def _form_for_class(
    form_class: "type[django_forms.Form]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    deps: _DepsState,
    bound: bool,
    action_name: str | None = None,
) -> "django_forms.Form":
    """Build the form through an overridden `get_form_kwargs`, else `get_initial`."""
    target = request if bound else None
    if getattr(form_class, "_has_get_form_kwargs", False):
        form_kwargs = _call_get_form_kwargs(
            form_class, request, url_kwargs, deps=deps, action_name=action_name
        )
        return _construct_form(form_class, form_kwargs, request=target)
    initial_data = _call_get_initial(
        form_class, request, url_kwargs, deps=deps, action_name=action_name
    )
    return _construct_form(
        form_class, _initial_form_kwargs(form_class, initial_data), request=target
    )


def _resolve_form_class(
    form_class: object,
    request: "HttpRequest",
    url_kwargs: dict[str, object],
    deps: _DepsState | None = None,
    action_name: str | None = None,
) -> "tuple[type[django_forms.Form], dict[str, Any]]":
    """Return `(form_class, init_kwargs)` for the dispatch.

    A factory's non-empty `(cls, init_kwargs)` bypasses both hooks into the constructor.
    """
    if isinstance(form_class, type):
        return cast("type[django_forms.Form]", form_class), {}
    if not callable(form_class):
        msg = (
            f"{_action_prefix(action_name)}form_class must be a Form subclass or a "
            f"callable factory, got {form_class!r}. Pass the form class itself or a "
            "factory that returns one."
        )
        raise TypeError(msg)
    cache, stack = deps if deps is not None else ({}, [])
    resolved = current_resolver().resolve_dependencies(
        form_class, request=request, _cache=cache, _stack=stack, **url_kwargs
    )
    produced = form_class(**resolved)
    if isinstance(produced, tuple) and len(produced) == _FACTORY_TUPLE_LEN:
        cls, init_kwargs = produced
        if isinstance(cls, type) and isinstance(init_kwargs, dict):
            return (
                cast("type[django_forms.Form]", cls),
                cast("dict[str, Any]", init_kwargs),
            )
    if not isinstance(produced, type):
        msg = (
            f"{_action_prefix(action_name)}form_class factory must return a Form "
            f"subclass or a (form_class, init_kwargs) tuple, got {produced!r}. Return "
            "the class itself, not an instance."
        )
        raise TypeError(msg)
    return cast("type[django_forms.Form]", produced), {}


def _form_action_context_callable(
    form_class: "type[django_forms.Form] | Callable[..., Any]",
) -> "Callable[..., types.SimpleNamespace]":
    """Return a callable building the unbound form namespace for page rendering.

    Given the cache of the page render, it reuses every row the page contexts loaded.
    """

    def context_func(
        request: "HttpRequest", dep_cache: dict[str, Any] | None = None
    ) -> types.SimpleNamespace:
        url_kwargs = url_kwargs_for_request(request)
        dep_cache = {} if dep_cache is None else dep_cache
        dep_stack: list[str] = []
        resolved_form_class, init_kwargs = _resolve_form_class(
            form_class, request, url_kwargs, (dep_cache, dep_stack)
        )
        if init_kwargs:
            form_instance = _construct_form(
                resolved_form_class, init_kwargs, request=None
            )
            return types.SimpleNamespace(form=form_instance)
        form_instance = _form_for_class(
            resolved_form_class,
            request,
            url_kwargs,
            deps=(dep_cache, dep_stack),
            bound=False,
        )
        return types.SimpleNamespace(form=form_instance)

    return context_func
