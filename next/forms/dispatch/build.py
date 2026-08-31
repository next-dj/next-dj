"""Form construction and hook invocation for the form action dispatch pipeline."""

import types
from typing import TYPE_CHECKING, Any, cast

from django.forms.models import BaseModelForm as DjangoBaseModelForm

from next.deps import resolver
from next.deps.resolver import cached_accepts_var_keyword
from next.forms.origin import _url_kwargs_for_request

from .responses import _is_model_instance


if TYPE_CHECKING:
    from collections.abc import Callable

    from django import forms as django_forms
    from django.http import HttpRequest


_FACTORY_TUPLE_LEN = 2

type _DepsState = tuple[dict[str, Any], list[str]]


def _build_form(
    form_class: "type[django_forms.Form]",
    initial_data: object,
    *,
    request: "HttpRequest | None",
    init_kwargs: dict[str, Any] | None = None,
) -> "django_forms.Form":
    """Build a form, bound to POST when `request` is given."""
    post_data = request.POST if request is not None else None
    files = request.FILES if request is not None and hasattr(request, "FILES") else None
    bound = request is not None
    if init_kwargs:
        if bound:
            return form_class(data=post_data, files=files, **init_kwargs)
        return form_class(**init_kwargs)
    if _is_model_instance(initial_data):
        if not issubclass(form_class, DjangoBaseModelForm):
            msg = (
                f"get_initial for {form_class.__name__} returned a "
                f"{type(initial_data).__name__} instance, but "
                f"{form_class.__name__} is not a ModelForm. Subclass "
                "next.forms.ModelForm to bind model instances."
            )
            raise TypeError(msg)
        if bound:
            return form_class(post_data, files, instance=initial_data)
        return form_class(instance=initial_data)
    initial = cast("dict[str, Any] | None", initial_data)
    if bound:
        return form_class(post_data, files, initial=initial)
    return form_class(initial=initial)


def _form_from_initial_data(
    form_class: "type[django_forms.Form]",
    initial_data: object,
    *,
    init_kwargs: dict[str, Any] | None = None,
) -> "django_forms.Form":
    """Build an unbound form from `get_initial` result (dict or model instance)."""
    return _build_form(form_class, initial_data, request=None, init_kwargs=init_kwargs)


def _bind_form_for_post(
    form_class: "type[django_forms.Form]",
    request: "HttpRequest",
    initial_data: object,
    *,
    init_kwargs: dict[str, Any] | None = None,
) -> "django_forms.Form":
    """Return a bound form for POST validation."""
    return _build_form(
        form_class, initial_data, request=request, init_kwargs=init_kwargs
    )


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
    resolved = resolver.resolve_dependencies(
        hook, request=request, _cache=cache, _stack=stack, **url_kwargs
    )
    if _accepts_var_keyword(hook):
        for key, value in url_kwargs.items():
            resolved.setdefault(key, value)
    return hook(**resolved)


def _call_get_initial(
    form_class: "type[django_forms.Form]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    deps: _DepsState,
    action_name: str | None = None,
) -> object:
    """Resolve `get_initial` dependencies and call it, feeding url_kwargs to kwargs."""
    if not hasattr(form_class, "get_initial"):
        prefix = f"Action {action_name!r}: " if action_name else ""
        msg = (
            f"{prefix}{form_class.__name__} has no get_initial method. "
            "Subclass next.forms.Form or next.forms.ModelForm, or define "
            "a get_initial classmethod."
        )
        raise TypeError(msg)
    return _resolve_and_call(form_class.get_initial, request, url_kwargs, deps=deps)


def _resolve_form_class(
    form_class: object,
    request: "HttpRequest",
    url_kwargs: dict[str, object],
    deps: _DepsState | None = None,
    action_name: str | None = None,
) -> "tuple[type[django_forms.Form], dict[str, Any]]":
    """Return `(form_class, init_kwargs)` for the dispatch.

    A factory may return a `Form` subclass or `(cls, init_kwargs)`. The latter bypasses
    `get_initial` and passes `**init_kwargs` to the form constructor.
    """
    if isinstance(form_class, type):
        return cast("type[django_forms.Form]", form_class), {}
    if not callable(form_class):
        prefix = f"Action {action_name!r}: " if action_name else ""
        msg = (
            f"{prefix}form_class must be a Form subclass or a callable "
            f"factory, got {form_class!r}. Pass the form class itself or "
            "a factory that returns one."
        )
        raise TypeError(msg)
    cache, stack = deps if deps is not None else ({}, [])
    resolved = resolver.resolve_dependencies(
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
        prefix = f"Action {action_name!r}: " if action_name else ""
        msg = (
            f"{prefix}form_class factory must return a Form subclass or a "
            f"(form_class, init_kwargs) tuple, got {produced!r}. Return the "
            "class itself, not an instance."
        )
        raise TypeError(msg)
    return cast("type[django_forms.Form]", produced), {}


def _form_action_context_callable(
    form_class: "type[django_forms.Form] | Callable[..., Any]",
) -> "Callable[[HttpRequest], types.SimpleNamespace]":
    """Return a callable building the unbound form namespace for page rendering."""

    def context_func(request: "HttpRequest") -> types.SimpleNamespace:
        url_kwargs = _url_kwargs_for_request(request)
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []
        resolved_form_class, init_kwargs = _resolve_form_class(
            form_class, request, url_kwargs, (dep_cache, dep_stack)
        )
        if init_kwargs:
            form_instance = _form_from_initial_data(
                resolved_form_class, None, init_kwargs=init_kwargs
            )
            return types.SimpleNamespace(form=form_instance)
        initial_data = _call_get_initial(
            resolved_form_class, request, url_kwargs, deps=(dep_cache, dep_stack)
        )
        form_instance = _form_from_initial_data(resolved_form_class, initial_data)
        return types.SimpleNamespace(form=form_instance)

    return context_func
