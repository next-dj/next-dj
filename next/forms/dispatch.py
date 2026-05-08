"""Shared POST pipeline and helpers for form action dispatch."""

from __future__ import annotations

import time
import types
from typing import TYPE_CHECKING, Any, cast

from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)

from next.deps import RESERVED_KEYS, resolver
from next.utils import caller_source_path

from .base import BaseModelForm
from .signals import action_dispatched, form_validation_failed
from .uid import validated_next_form_page_path


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest

    from .backends import FormActionBackend


def _get_caller_path(back_count: int = 1) -> Path:
    """Return the path of the module that called into us, skipping frames here."""
    return caller_source_path(
        back_count=back_count,
        max_walk=15,
        skip_while_filename_endswith=("forms.py", "dispatch.py", "decorators.py"),
    )


def _filter_reserved_url_kwargs(url_kwargs: dict[str, object]) -> dict[str, object]:
    """Drop keys that collide with DI names used by `resolve_dependencies`."""
    return {k: v for k, v in url_kwargs.items() if k not in RESERVED_KEYS}


def _url_kwargs_from_post(request: HttpRequest) -> dict[str, object]:
    """Parse `_url_param_*` hidden fields from POST."""
    out: dict[str, object] = {}
    if not hasattr(request, "POST"):
        return out
    for key, value in request.POST.items():
        if not key.startswith("_url_param_"):
            continue
        param_name = key.replace("_url_param_", "")
        if param_name in RESERVED_KEYS:
            continue
        if isinstance(value, str):
            try:
                out[param_name] = int(value)
            except ValueError:
                out[param_name] = value
        else:
            out[param_name] = value
    return out


def _url_kwargs_from_resolver_or_post(request: HttpRequest) -> dict[str, object]:
    """Return URL kwargs from the resolver match, otherwise from POST hidden fields."""
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match and getattr(resolver_match, "kwargs", None):
        return _filter_reserved_url_kwargs(dict(resolver_match.kwargs))
    if getattr(request, "method", None) == "POST" and hasattr(request, "POST"):
        return _url_kwargs_from_post(request)
    return {}


def _is_model_instance(obj: object) -> bool:
    """Return True when `obj` quacks like a Django model instance."""
    meta = getattr(obj, "_meta", None)
    return meta is not None and hasattr(meta, "model")


def _build_form(
    form_class: type[django_forms.Form],
    initial_data: object,
    *,
    request: HttpRequest | None,
) -> django_forms.Form:
    """Build a form, bound to POST data when `request` is given."""
    post_data = request.POST if request is not None else None
    files = request.FILES if request is not None and hasattr(request, "FILES") else None
    bound = request is not None
    if _is_model_instance(initial_data):
        if not issubclass(form_class, BaseModelForm):
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
    form_class: type[django_forms.Form],
    initial_data: object,
) -> django_forms.Form:
    """Build an unbound form from `get_initial` result (dict or model instance)."""
    return _build_form(form_class, initial_data, request=None)


def _form_action_context_callable(
    form_class: type[django_forms.Form],
) -> Callable[[HttpRequest], types.SimpleNamespace]:
    """Return a callable that builds a form instance for GET error rendering."""

    def context_func(request: HttpRequest) -> types.SimpleNamespace:
        if not hasattr(form_class, "get_initial"):
            msg = f"Form class {form_class} must have get_initial method"
            raise TypeError(msg)
        url_kwargs = _url_kwargs_from_resolver_or_post(request)
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []
        resolved = resolver.resolve_dependencies(
            form_class.get_initial,
            request=request,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        initial_data = form_class.get_initial(**resolved)
        form_instance = _form_from_initial_data(form_class, initial_data)
        return types.SimpleNamespace(form=form_instance)

    return context_func


def _bind_form_for_post(
    form_class: type[django_forms.Form],
    request: HttpRequest,
    initial_data: object,
) -> django_forms.Form:
    """Return a bound form for POST validation using initial or model instance."""
    return _build_form(form_class, initial_data, request=request)


def _normalize_handler_response(
    raw: HttpResponse | str | None | object,
) -> HttpResponse | str | None:
    """Coerce handler output to a string, response, redirect, or `None`."""
    if raw is None or isinstance(raw, (HttpResponse, str)):
        return raw
    if hasattr(raw, "url") and (url := getattr(raw, "url", None)):
        return HttpResponseRedirect(url)
    return None


class FormActionDispatch:
    """Shared POST pipeline and response shaping for backends."""

    @staticmethod
    def dispatch(
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        meta: dict[str, Any],
    ) -> HttpResponse:
        """Validate the form, run the handler, or re-render errors."""
        handler = meta["handler"]
        form_class = meta.get("form_class")

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        url_kwargs = _url_kwargs_from_post(request)
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []

        if form_class is None:
            return FormActionDispatch._dispatch_handler_only(
                handler,
                request,
                action_name,
                url_kwargs,
                dep_cache,
                dep_stack,
            )

        return FormActionDispatch._dispatch_with_form(
            backend,
            request,
            action_name,
            handler,
            form_class,
            url_kwargs,
            dep_cache,
            dep_stack,
        )

    @staticmethod
    def _dispatch_handler_only(  # noqa: PLR0913
        handler: Callable[..., Any],
        request: HttpRequest,
        action_name: str,
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> HttpResponse:
        resolved = resolver.resolve_dependencies(
            handler,
            request=request,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
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
            url_kwargs=dict(url_kwargs),
            duration_ms=duration_ms,
            response_status=response.status_code,
        )
        return response

    @staticmethod
    def _dispatch_with_form(  # noqa: PLR0913
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        handler: Callable[..., Any],
        form_class: type[django_forms.Form],
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> HttpResponse:
        if not hasattr(form_class, "get_initial"):
            msg = f"Form class {form_class} must have get_initial method"
            raise TypeError(msg)
        resolved = resolver.resolve_dependencies(
            form_class.get_initial,
            request=request,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        initial_data = form_class.get_initial(**resolved)
        form = _bind_form_for_post(form_class, request, initial_data)
        if not form.is_valid():
            if form_validation_failed.receivers:
                error_count = sum(len(errors) for errors in form.errors.values())
                form_validation_failed.send(
                    sender=FormActionDispatch,
                    action_name=action_name,
                    error_count=error_count,
                    field_names=tuple(form.errors.keys()),
                )
            return FormActionDispatch.form_response(
                backend, request, action_name, form, None
            )

        resolved = resolver.resolve_dependencies(
            handler,
            request=request,
            form=form,
            _cache=dep_cache,
            _stack=dep_stack,
            **url_kwargs,
        )
        start = time.perf_counter()
        raw = handler(**resolved)
        duration_ms = (time.perf_counter() - start) * 1000
        response = FormActionDispatch.ensure_http_response(
            _normalize_handler_response(raw),
            request=request,
            action_name=action_name,
            backend=backend,
        )
        action_dispatched.send(
            sender=FormActionDispatch,
            action_name=action_name,
            form=form,
            url_kwargs=dict(url_kwargs),
            duration_ms=duration_ms,
            response_status=response.status_code,
        )
        return response

    @staticmethod
    def form_response(
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None,
    ) -> HttpResponse:
        """Return full-page HTML for an invalid form submission."""
        page_path = validated_next_form_page_path(request)
        if page_path is None:
            return HttpResponseBadRequest("Missing or invalid _next_form_page")
        html = backend.render_form_fragment(
            request,
            action_name,
            form,
            template_fragment,
            page_file_path=page_path,
        )
        return HttpResponse(html)

    @staticmethod
    def render_form_fragment(  # noqa: PLR0913
        backend: FormActionBackend,
        request: HttpRequest,
        action_name: str,
        form: django_forms.Form | None,
        template_fragment: str | None,
        page_file_path: Path,
    ) -> str:
        """Delegate to `render_form_page_with_errors` for the given page file."""
        from .rendering import render_form_page_with_errors  # noqa: PLC0415

        return render_form_page_with_errors(
            backend,
            request,
            action_name,
            form,
            template_fragment,
            page_file_path,
        )

    @staticmethod
    def ensure_http_response(
        response: HttpResponse | str | None,
        request: HttpRequest | None = None,
        action_name: str | None = None,
        backend: FormActionBackend | None = None,
    ) -> HttpResponse:
        """Coerce `None`, `str`, or `HttpResponse` into an `HttpResponse`."""
        response = _normalize_handler_response(response)

        if response is None:
            if request and action_name and backend:
                return FormActionDispatch.form_response(
                    backend, request, action_name, None, None
                )
            return HttpResponse(status=204)
        if isinstance(response, HttpResponse):
            return response
        return HttpResponse(response)


def build_form_namespace_for_action(
    action_name: str,
    request: HttpRequest,
) -> types.SimpleNamespace | None:
    """Build the `SimpleNamespace(form=...)` used by `{% form %}` when lazy."""
    from .manager import form_action_manager  # noqa: PLC0415

    form_action_manager._ensure_backends()
    for backend in form_action_manager._backends:
        meta = backend.get_meta(action_name)
        if meta is None:
            continue
        fc = meta.get("form_class")
        if fc is None:
            return None
        return _form_action_context_callable(fc)(request)
    return None


__all__ = [
    "FormActionDispatch",
    "_bind_form_for_post",
    "_filter_reserved_url_kwargs",
    "_form_action_context_callable",
    "_form_from_initial_data",
    "_get_caller_path",
    "_normalize_handler_response",
    "_url_kwargs_from_post",
    "_url_kwargs_from_resolver_or_post",
    "build_form_namespace_for_action",
]
