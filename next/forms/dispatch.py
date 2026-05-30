"""POST dispatch pipeline for form actions."""

import inspect
import time
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from django.forms.models import BaseModelForm as DjangoBaseModelForm
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)

from next.deps import REQUEST_DEP_CACHE_ATTR, resolver
from next.deps.resolver import _cached_signature
from next.utils import caller_source_path

from ._request_utils import (
    _url_kwargs_from_post,
    _url_kwargs_from_resolver_or_post,
)
from .rendering import render_form_page_with_errors
from .signals import (
    action_dispatched,
    form_validation_failed,
    wizard_completed,
    wizard_step_submitted,
)
from .uid import _validated_origin_path, validated_next_form_page_path


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest

    from .backends import FormActionBackend
    from .base import BaseForm as NextBaseForm


_FACTORY_TUPLE_LEN = 2
_HTTP_ERROR_FLOOR = 400


def _get_caller_path(back_count: int = 1) -> "Path":
    """Return the path of the module that called into us, skipping frames here."""
    return caller_source_path(
        back_count=back_count,
        max_walk=15,
        skip_while_filename_endswith=("forms.py", "dispatch.py", "decorators.py"),
    )


def _is_model_instance(obj: object) -> bool:
    """Return True when `obj` quacks like a Django model instance."""
    meta = getattr(obj, "_meta", None)
    return meta is not None and hasattr(meta, "model")


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
            msg = "instance parameter only supported for ModelForm"
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


def _accepts_var_keyword(func: "Callable[..., Any]") -> bool:
    """Return True when `func` declares a `**kwargs` parameter."""
    try:
        sig = _cached_signature(func)
    except (TypeError, ValueError):
        return False
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _call_get_initial(
    form_class: "type[django_forms.Form]",
    request: "HttpRequest",
    url_kwargs: dict[str, Any],
    *,
    cache: dict[str, Any],
    stack: list[str],
) -> object:
    """Resolve `get_initial` dependencies and call it, feeding url_kwargs to kwargs."""
    if not hasattr(form_class, "get_initial"):
        msg = f"Form class {form_class} must have get_initial method"
        raise TypeError(msg)
    get_initial = form_class.get_initial
    resolved = resolver.resolve_dependencies(
        get_initial,
        request=request,
        _cache=cache,
        _stack=stack,
        **url_kwargs,
    )
    if _accepts_var_keyword(get_initial):
        for key, value in url_kwargs.items():
            resolved.setdefault(key, value)
    return get_initial(**resolved)


def _form_action_context_callable(
    form_class: "type[django_forms.Form]",
) -> "Callable[[HttpRequest], types.SimpleNamespace]":
    """Return a callable that builds a form instance for GET error rendering."""

    def context_func(request: "HttpRequest") -> types.SimpleNamespace:
        url_kwargs = _url_kwargs_from_resolver_or_post(request)
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []
        resolved_form_class, init_kwargs = _resolve_form_class(
            form_class,
            request,
            url_kwargs,
            dep_cache,
            dep_stack,
        )
        if init_kwargs:
            form_instance = _form_from_initial_data(
                resolved_form_class, None, init_kwargs=init_kwargs
            )
            return types.SimpleNamespace(form=form_instance)
        initial_data = _call_get_initial(
            resolved_form_class, request, url_kwargs, cache=dep_cache, stack=dep_stack
        )
        form_instance = _form_from_initial_data(resolved_form_class, initial_data)
        return types.SimpleNamespace(form=form_instance)

    return context_func


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


def _resolve_form_class(
    form_class: object,
    request: "HttpRequest",
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any] | None = None,
    dep_stack: list[str] | None = None,
) -> "tuple[type[django_forms.Form], dict[str, Any]]":
    """Return `(form_class, init_kwargs)` for the dispatch.

    A factory may return a `Form` subclass or `(cls, init_kwargs)`. The
    latter bypasses `get_initial` and passes `**init_kwargs` to the form
    constructor.
    """
    if isinstance(form_class, type):
        return cast("type[django_forms.Form]", form_class), {}
    if not callable(form_class):
        msg = f"form_class must be a Form subclass or callable, got {form_class!r}"
        raise TypeError(msg)
    cache = dep_cache if dep_cache is not None else {}
    stack = dep_stack if dep_stack is not None else []
    resolved = resolver.resolve_dependencies(
        form_class,
        request=request,
        _cache=cache,
        _stack=stack,
        **url_kwargs,
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
        msg = f"form_class factory must return a Form subclass, got {produced!r}"
        raise TypeError(msg)
    return cast("type[django_forms.Form]", produced), {}


def _normalize_handler_response(
    raw: "HttpResponse | str | None | object",
) -> "HttpResponse | str | None":
    """Coerce handler output to a string, response, redirect, or `None`."""
    if raw is None or isinstance(raw, (HttpResponse, str)):
        return raw
    if hasattr(raw, "url") and (url := getattr(raw, "url", None)):
        return HttpResponseRedirect(url)
    return None


@dataclass
class _DispatchState:
    """Bundle of mutable dispatch state threaded through helper methods."""

    url_kwargs: dict[str, object]
    dep_cache: dict[str, Any]
    dep_stack: list[str]


@dataclass
class _FormDispatchParams:
    """Bundle of form-specific params threaded into `_dispatch_with_form`."""

    action_name: str
    handler: "Callable[..., Any] | None"
    form_class: "type[django_forms.Form]"
    init_kwargs: dict[str, Any] = field(default_factory=dict)


class FormActionDispatch:
    """Shared POST pipeline and response shaping for backends."""

    @staticmethod
    def dispatch(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        meta: dict[str, Any],
    ) -> HttpResponse:
        """Validate the form, run the handler, or re-render errors."""
        handler = meta.get("handler")
        form_class = meta.get("form_class")
        wizard_class = meta.get("wizard_class")

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        state = _DispatchState(
            url_kwargs=_url_kwargs_from_post(request),
            dep_cache={},
            dep_stack=[],
        )
        setattr(request, REQUEST_DEP_CACHE_ATTR, state.dep_cache)

        if wizard_class is not None:
            return FormActionDispatch._dispatch_wizard(
                backend, request, action_name, wizard_class, state
            )

        if form_class is None and handler is not None:
            return FormActionDispatch._dispatch_handler_only(
                handler,
                request,
                action_name,
                state,
            )

        if form_class is not None:
            resolved_form_class, init_kwargs = _resolve_form_class(
                form_class,
                request,
                state.url_kwargs,
                state.dep_cache,
                state.dep_stack,
            )
            params = _FormDispatchParams(
                action_name=action_name,
                handler=handler,
                form_class=resolved_form_class,
                init_kwargs=init_kwargs,
            )
            return FormActionDispatch._dispatch_with_form(
                backend, request, params, state
            )

        return HttpResponseBadRequest("Invalid action configuration")

    @staticmethod
    def _dispatch_handler_only(
        handler: "Callable[..., Any]",
        request: "HttpRequest",
        action_name: str,
        state: _DispatchState,
    ) -> HttpResponse:
        resolved = resolver.resolve_dependencies(
            handler,
            request=request,
            _cache=state.dep_cache,
            _stack=state.dep_stack,
            **state.url_kwargs,
        )
        start = time.perf_counter()
        raw = handler(**resolved)
        duration_ms = (time.perf_counter() - start) * 1000
        response = FormActionDispatch.ensure_http_response(
            _normalize_handler_response(raw),
            request=request,
        )
        action_dispatched.send(
            sender=FormActionDispatch,
            action_name=action_name,
            form=None,
            url_kwargs=dict(state.url_kwargs),
            duration_ms=duration_ms,
            response_status=response.status_code,
            dep_cache=dict(state.dep_cache),
        )
        return response

    @staticmethod
    def _dispatch_with_form(
        backend: "FormActionBackend",
        request: "HttpRequest",
        params: _FormDispatchParams,
        state: _DispatchState,
    ) -> HttpResponse:
        if params.init_kwargs:
            form = _bind_form_for_post(
                params.form_class, request, None, init_kwargs=params.init_kwargs
            )
        else:
            initial_data = _call_get_initial(
                params.form_class,
                request,
                state.url_kwargs,
                cache=state.dep_cache,
                stack=state.dep_stack,
            )
            form = _bind_form_for_post(params.form_class, request, initial_data)
        if not form.is_valid():
            if form_validation_failed.receivers:
                error_count = sum(len(errors) for errors in form.errors.values())
                form_validation_failed.send(
                    sender=FormActionDispatch,
                    action_name=params.action_name,
                    error_count=error_count,
                    field_names=tuple(form.errors.keys()),
                )
            return FormActionDispatch.form_response(
                backend, request, params.action_name, form
            )

        if params.handler is None:
            next_form = cast("NextBaseForm", form)
            resolved = resolver.resolve_dependencies(
                next_form.on_valid,
                request=request,
                _cache=state.dep_cache,
                _stack=state.dep_stack,
                **state.url_kwargs,
            )
            start = time.perf_counter()
            raw = next_form.on_valid(**resolved)
        else:
            resolved = resolver.resolve_dependencies(
                params.handler,
                request=request,
                form=form,
                _cache=state.dep_cache,
                _stack=state.dep_stack,
                **state.url_kwargs,
            )
            start = time.perf_counter()
            raw = params.handler(**resolved)

        duration_ms = (time.perf_counter() - start) * 1000
        response = FormActionDispatch.ensure_http_response(
            _normalize_handler_response(raw),
            request=request,
            action_name=params.action_name,
            backend=backend,
        )
        action_dispatched.send(
            sender=FormActionDispatch,
            action_name=params.action_name,
            form=form,
            url_kwargs=dict(state.url_kwargs),
            duration_ms=duration_ms,
            response_status=response.status_code,
            dep_cache=dict(state.dep_cache),
        )
        return response

    @staticmethod
    def _dispatch_wizard(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        wizard_class: type,
        state: _DispatchState,
    ) -> HttpResponse:
        """Validate the current wizard step, then route forward or finalise."""
        origin = _validated_origin_path(request.POST.get("_next_form_origin"))
        if origin is None:
            return HttpResponseBadRequest("Missing or invalid _next_form_origin")
        wizard = wizard_class(
            request=request, url_kwargs=state.url_kwargs, base_path=origin
        )
        step_name = wizard.current_step()
        form_class = wizard.step_form_class(step_name)
        if form_class is None:
            return HttpResponseBadRequest("Unknown wizard step")

        form_kwargs = wizard.get_form_kwargs()
        files = request.FILES if hasattr(request, "FILES") else None
        form = form_class(request.POST, files, **form_kwargs)
        if not form.is_valid():
            if form_validation_failed.receivers:
                error_count = sum(len(errors) for errors in form.errors.values())
                form_validation_failed.send(
                    sender=FormActionDispatch,
                    action_name=action_name,
                    error_count=error_count,
                    field_names=tuple(form.errors.keys()),
                )
            return FormActionDispatch.form_response(backend, request, action_name, form)

        cleaned = dict(form.cleaned_data)
        wizard.save_step(step_name, cleaned)
        wizard_step_submitted.send(
            sender=FormActionDispatch,
            wizard_class=wizard_class,
            step=step_name,
            cleaned_data=cleaned,
        )

        next_step = wizard.next_step(step_name)
        if next_step is None:
            merged = wizard.cleaned_data_so_far()
            start = time.perf_counter()
            raw = wizard.done(request, merged)
            duration_ms = (time.perf_counter() - start) * 1000
            response = FormActionDispatch.ensure_http_response(
                _normalize_handler_response(raw),
                request=request,
                action_name=action_name,
                backend=backend,
            )
            if response.status_code < _HTTP_ERROR_FLOOR:
                wizard.clear_storage()
                wizard_completed.send(
                    sender=FormActionDispatch,
                    wizard_class=wizard_class,
                    cleaned_data=merged,
                )
        else:
            response = HttpResponseRedirect(wizard.goto(next_step))
            duration_ms = 0.0

        action_dispatched.send(
            sender=FormActionDispatch,
            action_name=action_name,
            form=form,
            url_kwargs=dict(state.url_kwargs),
            duration_ms=duration_ms,
            response_status=response.status_code,
            dep_cache=dict(state.dep_cache),
        )
        return response

    @staticmethod
    def form_response(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
    ) -> HttpResponse:
        """Return full-page HTML for an invalid form submission."""
        page_path = validated_next_form_page_path(request)
        if page_path is None:
            return HttpResponseBadRequest("Missing or invalid _next_form_page")
        html = backend.render_form_fragment(request, action_name, form, page_path)
        return HttpResponse(html)

    @staticmethod
    def render_form_fragment(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
        page_file_path: "Path",
    ) -> str:
        """Delegate to `render_form_page_with_errors` for the given page file."""
        return render_form_page_with_errors(
            backend,
            request,
            action_name,
            form,
            page_file_path,
        )

    @staticmethod
    def ensure_http_response(
        response: "HttpResponse | str | None",
        request: "HttpRequest | None" = None,
        action_name: str | None = None,
        backend: "FormActionBackend | None" = None,
    ) -> HttpResponse:
        """Coerce `None`, `str`, or `HttpResponse` into an `HttpResponse`."""
        response = _normalize_handler_response(response)

        if response is None:
            if request and action_name and backend:
                return FormActionDispatch.form_response(
                    backend, request, action_name, None
                )
            return HttpResponse(status=204)
        if isinstance(response, HttpResponse):
            return response
        return HttpResponse(response)


__all__ = ["FormActionDispatch"]
