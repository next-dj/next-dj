"""POST dispatch pipeline for form actions."""

import inspect
import time
import types
import warnings
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.forms.models import BaseModelForm as DjangoBaseModelForm
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
    QueryDict,
)
from django.shortcuts import resolve_url

from next.deps import REQUEST_DEP_CACHE_ATTR, resolver
from next.deps.resolver import cached_signature

from ._request_utils import (
    _resolve_origin,
    _url_kwargs_for_request,
)
from .signals import (
    action_dispatched,
    form_validation_failed,
    wizard_completed,
    wizard_step_submitted,
)
from .uid import validated_origin_path


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest

    from ._request_utils import _OriginMatch
    from .backends import ActionGuard, ActionMeta, FormActionBackend
    from .base import BaseForm as NextBaseForm
    from .wizard import FormWizard


_FACTORY_TUPLE_LEN = 2
_HTTP_ERROR_FLOOR = 400


def _is_model_instance(obj: object) -> bool:
    """Return True when `obj` quacks like a Django model instance."""
    meta = getattr(obj, "_meta", None)
    return meta is not None and hasattr(meta, "model")


def _redirect_to_login(next_url: str) -> HttpResponseRedirect:
    """Build the LOGIN_URL redirect carrying `next_url`.

    Mirrors `django.contrib.auth.views.redirect_to_login` without importing
    contrib.auth.views, whose module-level `get_user_model()` call requires
    django.contrib.auth in INSTALLED_APPS.
    """
    scheme, netloc, path, query, fragment = urlsplit(resolve_url(settings.LOGIN_URL))
    querystring = QueryDict(query, mutable=True)
    querystring[REDIRECT_FIELD_NAME] = next_url
    return HttpResponseRedirect(
        urlunsplit((scheme, netloc, path, querystring.urlencode(safe="/"), fragment))
    )


def _check_access(
    request: "HttpRequest",
    guard: "ActionGuard",
) -> HttpResponseRedirect | None:
    """Enforce the action guard with `AccessMixin` semantics.

    Anonymous users get a login redirect whose `next` is the validated posted
    origin, authenticated users missing a permission raise PermissionDenied.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        origin = validated_origin_path(request.POST.get("_next_form_origin"))
        return _redirect_to_login(origin or "/")
    if guard.permissions and not user.has_perms(guard.permissions):
        raise PermissionDenied
    return None


def _send_success_message(
    request: "HttpRequest",
    source: object,
    cleaned_data: dict[str, Any],
) -> None:
    """Flash the declared success message through django.contrib.messages."""
    get_message = getattr(source, "get_success_message", None)
    if get_message is None:
        return
    message = get_message(cleaned_data)
    if message:
        messages.success(request, message)


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
        sig = cached_signature(func)
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
    form_class: "type[django_forms.Form] | Callable[..., Any]",
) -> "Callable[[HttpRequest], types.SimpleNamespace]":
    """Return a callable that builds a form instance for GET error rendering."""

    def context_func(request: "HttpRequest") -> types.SimpleNamespace:
        url_kwargs = _url_kwargs_for_request(request)
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
    if _is_model_instance(raw):
        get_absolute_url = getattr(raw, "get_absolute_url", None)
        if get_absolute_url is not None:
            # CreateView-style idiom: a returned model instance redirects
            # to its canonical URL.
            return HttpResponseRedirect(str(get_absolute_url()))
    # The isinstance check above runs first by contract: every rich return
    # type the framework ships must subclass HttpResponse. The `.url` sniff
    # below is last-resort sugar for model-like objects, never a primary
    # extension point.
    if hasattr(raw, "url") and (url := getattr(raw, "url", None)):
        return HttpResponseRedirect(url)
    warnings.warn(
        f"form action handler returned unsupported {type(raw).__name__}, "
        "treating it as None (origin re-render or 204)",
        RuntimeWarning,
        stacklevel=2,
    )
    return None


class ActionOutcomeKind(StrEnum):
    """Discriminator for the pipeline outcomes a backend shapes into responses."""

    RESULT = "result"
    INVALID = "invalid"
    WIZARD_ADVANCE = "wizard_advance"


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionOutcome:
    """One pipeline decision waiting to be shaped into an HTTP response.

    Fields may be added in future versions, construct with keywords only.
    """

    kind: ActionOutcomeKind
    action_name: str
    uid: str | None = None
    raw: Any = None
    form: "django_forms.Form | None" = None
    redirect_to: str | None = None
    url_kwargs: dict[str, object] | None = None
    wizard: "FormWizard | None" = None
    page_path: "Path | None" = None
    origin: str | None = None


@dataclass
class _DispatchState:
    """Bundle of mutable dispatch state threaded through helper methods."""

    url_kwargs: dict[str, object]
    dep_cache: dict[str, Any]
    dep_stack: list[str]
    uid: str | None = None
    origin_match: "_OriginMatch | None" = None

    @property
    def page_path(self) -> "Path | None":
        """Return the origin page source path, if the origin resolved."""
        return self.origin_match.page_path if self.origin_match else None

    @property
    def origin(self) -> str | None:
        """Return the validated origin URL path, if the origin resolved."""
        return self.origin_match.origin if self.origin_match else None

    def emit_action_dispatched(
        self,
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
        duration_ms: float,
        response: HttpResponse,
    ) -> None:
        """Send `action_dispatched` when any receiver is connected."""
        if action_dispatched.receivers:
            action_dispatched.send(
                sender=FormActionDispatch,
                action_name=action_name,
                uid=self.uid,
                request=request,
                form=form,
                url_kwargs=dict(self.url_kwargs),
                duration_ms=duration_ms,
                response_status=response.status_code,
                dep_cache=dict(self.dep_cache),
            )

    def emit_form_validation_failed(
        self,
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form",
    ) -> None:
        """Send `form_validation_failed` when any receiver is connected."""
        if form_validation_failed.receivers:
            error_count = sum(len(errors) for errors in form.errors.values())
            form_validation_failed.send(
                sender=FormActionDispatch,
                action_name=action_name,
                uid=self.uid,
                request=request,
                error_count=error_count,
                field_names=tuple(form.errors.keys()),
            )

    def emit_wizard_step_submitted(
        self,
        request: "HttpRequest",
        wizard_class: "type[FormWizard]",
        step_name: str,
        cleaned: dict[str, Any],
    ) -> None:
        """Send `wizard_step_submitted` when any receiver is connected."""
        if wizard_step_submitted.receivers:
            wizard_step_submitted.send(
                sender=FormActionDispatch,
                wizard_class=wizard_class,
                step=step_name,
                cleaned_data=cleaned,
                uid=self.uid,
                request=request,
            )

    def emit_wizard_completed(
        self,
        request: "HttpRequest",
        wizard_class: "type[FormWizard]",
        merged: dict[str, Any],
    ) -> None:
        """Send `wizard_completed` when any receiver is connected."""
        if wizard_completed.receivers:
            wizard_completed.send(
                sender=FormActionDispatch,
                wizard_class=wizard_class,
                cleaned_data=merged,
                uid=self.uid,
                request=request,
            )


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
        meta: "ActionMeta",
    ) -> HttpResponse:
        """Validate the form, run the handler, or re-render errors."""
        handler = meta.get("handler")
        form_class = meta.get("form_class")
        wizard_class = meta.get("wizard_class")

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        guard = meta.get("guard")
        if guard is not None:
            denial = _check_access(request, guard)
            if denial is not None:
                return denial

        origin_match = _resolve_origin(request)
        state = _DispatchState(
            url_kwargs=dict(origin_match.url_kwargs) if origin_match else {},
            dep_cache={},
            dep_stack=[],
            uid=meta.get("uid"),
            origin_match=origin_match,
        )
        setattr(request, REQUEST_DEP_CACHE_ATTR, state.dep_cache)

        if wizard_class is not None:
            return FormActionDispatch._dispatch_wizard(
                backend, request, action_name, wizard_class, state
            )

        if form_class is None and handler is not None:
            return FormActionDispatch._dispatch_handler_only(
                backend,
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
        backend: "FormActionBackend",
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
        response = backend.shape_response(
            request,
            ActionOutcome(
                kind=ActionOutcomeKind.RESULT,
                action_name=action_name,
                uid=state.uid,
                raw=raw,
            ),
        )
        state.emit_action_dispatched(request, action_name, None, duration_ms, response)
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
            state.emit_form_validation_failed(request, params.action_name, form)
            return backend.shape_response(
                request,
                ActionOutcome(
                    kind=ActionOutcomeKind.INVALID,
                    action_name=params.action_name,
                    uid=state.uid,
                    form=form,
                    url_kwargs=state.url_kwargs,
                    page_path=state.page_path,
                    origin=state.origin,
                ),
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
        response = backend.shape_response(
            request,
            ActionOutcome(
                kind=ActionOutcomeKind.RESULT,
                action_name=params.action_name,
                uid=state.uid,
                raw=raw,
                form=form,
            ),
        )
        if response.status_code < _HTTP_ERROR_FLOOR:
            _send_success_message(request, form, form.cleaned_data)
        state.emit_action_dispatched(
            request, params.action_name, form, duration_ms, response
        )
        return response

    @staticmethod
    def _dispatch_wizard(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        wizard_class: "type[FormWizard]",
        state: _DispatchState,
    ) -> HttpResponse:
        """Validate the current wizard step, then route forward or finalise."""
        if state.origin_match is None:
            return HttpResponseBadRequest("Missing or invalid _next_form_origin")
        wizard = wizard_class(
            request=request,
            url_kwargs=state.url_kwargs,
            base_path=state.origin_match.origin,
        )
        step_name = wizard.current_step()
        form_class = wizard.step_form_class(step_name)
        if form_class is None:
            return HttpResponseBadRequest("Unknown wizard step")

        form_kwargs = wizard.get_form_kwargs(step_name)
        files = request.FILES if hasattr(request, "FILES") else None
        form = form_class(request.POST, files, **form_kwargs)
        if not form.is_valid():
            state.emit_form_validation_failed(request, action_name, form)
            return backend.shape_response(
                request,
                ActionOutcome(
                    kind=ActionOutcomeKind.INVALID,
                    action_name=action_name,
                    uid=state.uid,
                    form=form,
                    url_kwargs=state.url_kwargs,
                    wizard=wizard,
                    page_path=state.page_path,
                    origin=state.origin,
                ),
            )

        cleaned = dict(form.cleaned_data)
        wizard.save_step(step_name, cleaned)
        state.emit_wizard_step_submitted(request, wizard_class, step_name, cleaned)

        next_step = wizard.next_step(step_name)
        if next_step is None:
            # A direct POST to the last step must not finalise while an
            # earlier step has no stored data, so reroute to the first gap.
            next_step = wizard.first_incomplete_step()
        if next_step is None:
            merged = wizard.get_all_cleaned_data()
            resolved = resolver.resolve_dependencies(
                wizard.done,
                request=request,
                cleaned_data=merged,
                _cache=state.dep_cache,
                _stack=state.dep_stack,
                **state.url_kwargs,
            )
            start = time.perf_counter()
            raw = wizard.done(**resolved)
            duration_ms = (time.perf_counter() - start) * 1000
            response = backend.shape_response(
                request,
                ActionOutcome(
                    kind=ActionOutcomeKind.RESULT,
                    action_name=action_name,
                    uid=state.uid,
                    raw=raw,
                    form=form,
                    wizard=wizard,
                ),
            )
            if response.status_code < _HTTP_ERROR_FLOOR:
                _send_success_message(request, wizard, merged)
                wizard.clear_storage()
                state.emit_wizard_completed(request, wizard_class, merged)
        else:
            response = backend.shape_response(
                request,
                ActionOutcome(
                    kind=ActionOutcomeKind.WIZARD_ADVANCE,
                    action_name=action_name,
                    uid=state.uid,
                    redirect_to=wizard.goto(next_step),
                    wizard=wizard,
                ),
            )
            duration_ms = 0.0

        state.emit_action_dispatched(request, action_name, form, duration_ms, response)
        return response

    @staticmethod
    def shape_response(
        backend: "FormActionBackend",
        request: "HttpRequest",
        outcome: ActionOutcome,
    ) -> HttpResponse:
        """Build the default envelope for one pipeline outcome.

        Invalid submissions re-render the origin page with HTTP 200 and the
        `X-Next-Form`/`X-Next-Action` headers, wizard advances redirect.
        """
        if outcome.kind == ActionOutcomeKind.INVALID:
            if outcome.page_path is None:
                return HttpResponseBadRequest("Missing or invalid _next_form_origin")
            html = backend.render_invalid_page(
                request,
                outcome.action_name,
                outcome.form,
                outcome.page_path,
                outcome.url_kwargs,
            )
            response = HttpResponse(html)
            if outcome.form is not None:
                # A None-returning handler re-renders the origin through this
                # same branch with form=None, and that success path must stay
                # unmarked for clients branching on the headers.
                response["X-Next-Form"] = "invalid"
                if outcome.uid:
                    response["X-Next-Action"] = outcome.uid
            return response
        if outcome.kind == ActionOutcomeKind.WIZARD_ADVANCE:
            return HttpResponseRedirect(cast("str", outcome.redirect_to))
        if outcome.form is None:
            return FormActionDispatch.ensure_http_response(outcome.raw, request=request)
        return FormActionDispatch.ensure_http_response(
            outcome.raw,
            request=request,
            action_name=outcome.action_name,
            backend=backend,
        )

    @staticmethod
    def _form_response(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        form: "django_forms.Form | None",
    ) -> HttpResponse:
        """Return full-page HTML for an invalid form submission."""
        origin_match = _resolve_origin(request)
        return backend.shape_response(
            request,
            ActionOutcome(
                kind=ActionOutcomeKind.INVALID,
                action_name=action_name,
                form=form,
                url_kwargs=dict(origin_match.url_kwargs) if origin_match else None,
                page_path=origin_match.page_path if origin_match else None,
                origin=origin_match.origin if origin_match else None,
            ),
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
                return FormActionDispatch._form_response(
                    backend, request, action_name, None
                )
            return HttpResponse(status=204)
        if isinstance(response, HttpResponse):
            return response
        return HttpResponse(response)


__all__ = ["ActionOutcome", "ActionOutcomeKind", "FormActionDispatch"]
