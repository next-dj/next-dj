"""POST dispatch pipeline for form actions."""

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)

from next.deps import REQUEST_DEP_CACHE_ATTR, UnboundObjectError
from next.deps.resolver import current_resolver
from next.forms.origin import resolve_origin
from next.forms.signals import (
    action_dispatched,
    form_validation_failed,
    wizard_completed,
    wizard_step_submitted,
)

from .build import _construct_form, _form_for_class, _resolve_form_class
from .permissions import (
    _check_access,
    _check_page_access,
    _check_tree_access,
    _enforce_object_permissions,
    _enforce_registered_permissions,
    _enforce_view_permissions,
    _object_permissions,
)
from .responses import (
    _HTTP_ERROR_FLOOR,
    ActionOutcome,
    ActionOutcomeKind,
    _flash_success_before_rerender,
    _send_success_message,
    ensure_http_response,
)
from .wizard import _dispatch_wizard, _maybe_validate_only


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest, HttpResponseBase

    from next.forms.backends import ActionMeta, FormActionBackend
    from next.forms.base import BaseForm as NextBaseForm
    from next.forms.origin import OriginMatch
    from next.forms.wizard import FormWizard


logger = logging.getLogger(__name__)

type _Branch = Literal["wizard", "handler", "form"]


class FormActionDispatch:
    """Shared POST pipeline and response shaping for backends.

    It sends `action_dispatched`, `form_validation_failed` and `form_access_denied` with
    itself as the sender, and the wizard signals with the wizard class.
    """

    # The documented address a custom backend that drives the pipeline by
    # hand calls, so the alias is part of the public surface.
    ensure_http_response = staticmethod(ensure_http_response)

    @staticmethod
    def dispatch(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        meta: "ActionMeta",
    ) -> "HttpResponseBase":
        """Validate the form, run the handler, or re-render errors.

        The posted origin page authorizes the request before anything renders it, so a
        submission never reaches a page body the requester would be refused on a visit.
        """
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        try:
            return FormActionDispatch._admitted(backend, request, action_name, meta)
        except UnboundObjectError as exc:
            if exc.parent:
                raise
            logger.warning(
                "action %r received an origin without the URL kwarg %r",
                action_name,
                exc.key,
            )
            return HttpResponseBadRequest(
                f"Action {action_name!r} needs a URL kwarg the origin lacks."
            )

    @staticmethod
    def _admitted(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        meta: "ActionMeta",
    ) -> "HttpResponseBase":
        """Route the action once the page tree, its guard and the origin admit it."""
        origin_match = resolve_origin(request)
        dep_cache: dict[str, Any] = {}
        refusal = FormActionDispatch._refuse_access(
            request, meta, origin_match, action_name, dep_cache
        )
        if refusal is not None:
            return refusal
        state = _DispatchState(
            url_kwargs=dict(origin_match.url_kwargs) if origin_match else {},
            dep_cache=dep_cache,
            dep_stack=[],
            uid=meta.get("uid"),
            origin_match=origin_match,
        )
        setattr(request, REQUEST_DEP_CACHE_ATTR, state.dep_cache)
        return FormActionDispatch._route(backend, request, action_name, meta, state)

    @staticmethod
    def _route(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        meta: "ActionMeta",
        state: "_DispatchState",
    ) -> "HttpResponseBase":
        """Run the wizard, handler-only or form branch the action declares.

        Every URL kwarg comes from the posted origin, so a miss at any depth is a 400.
        """
        branch = _branch(meta)
        if branch == "wizard":
            wizard_class = cast("type[FormWizard]", meta.get("wizard_class"))
            return _dispatch_wizard(backend, request, action_name, wizard_class, state)
        if branch == "handler":
            handler = cast("Callable[..., Any]", meta.get("handler"))
            return FormActionDispatch._dispatch_handler_only(
                backend, handler, request, action_name, state
            )
        if branch == "form":
            return FormActionDispatch._dispatch_form_class(
                backend, request, action_name, meta, state
            )

        # The registration path names a server location, so the client never reads it.
        logger.warning(
            "action %r declared in %s has no handler, form_class, or wizard_class",
            action_name,
            meta.get("file_path"),
        )
        return HttpResponseBadRequest(
            f"Action {action_name!r} has no handler, form_class, or wizard_class."
        )

    @staticmethod
    def _refuse_access(
        request: "HttpRequest",
        meta: "ActionMeta",
        origin_match: "OriginMatch | None",
        action_name: str,
        dep_cache: dict[str, Any],
    ) -> "HttpResponseBase | None":
        """Return the denial of the page tree, the action guard or the origin page.

        The rule of the owner and of the origin page answers before the guard of `Meta`.
        """
        denial = _check_tree_access(
            request,
            meta.get("file_path"),
            origin_match,
            dep_cache,
            action_name=action_name,
            uid=meta.get("uid"),
            sender=_DispatchState.signal_sender,
        )
        if denial is not None:
            return denial
        guard = meta.get("guard")
        if guard is not None:
            object_stage = bool(guard.permissions) and _branch(meta) == "form"
            denial = _check_access(request, guard, object_stage=object_stage)
            if denial is not None:
                return denial
        return _check_page_access(
            request,
            origin_match,
            action_name=action_name,
            uid=meta.get("uid"),
            sender=_DispatchState.signal_sender,
        )

    @staticmethod
    def _dispatch_form_class(
        backend: "FormActionBackend",
        request: "HttpRequest",
        action_name: str,
        meta: "ActionMeta",
        state: "_DispatchState",
    ) -> HttpResponse:
        """Resolve the form class, enforce the view hook, then bind and dispatch."""
        resolved_form_class, init_kwargs = _resolve_form_class(
            meta.get("form_class"),
            request,
            state.url_kwargs,
            (state.dep_cache, state.dep_stack),
            action_name=action_name,
        )
        denial = _enforce_view_permissions(
            resolved_form_class, request, action_name, state
        )
        if denial is not None:
            return denial
        params = _FormDispatchParams(
            action_name=action_name,
            handler=meta.get("handler"),
            form_class=resolved_form_class,
            init_kwargs=init_kwargs,
            object_permissions=_object_permissions(meta.get("guard")),
        )
        return FormActionDispatch._dispatch_with_form(backend, request, params, state)

    @staticmethod
    def _dispatch_handler_only(
        backend: "FormActionBackend",
        handler: "Callable[..., Any]",
        request: "HttpRequest",
        action_name: str,
        state: "_DispatchState",
    ) -> HttpResponse:
        resolved = current_resolver().resolve_dependencies(
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
        params: "_FormDispatchParams",
        state: "_DispatchState",
    ) -> HttpResponse:
        if params.init_kwargs:
            form = _construct_form(
                params.form_class, params.init_kwargs, request=request
            )
        else:
            form = _form_for_class(
                params.form_class,
                request,
                state.url_kwargs,
                deps=(state.dep_cache, state.dep_stack),
                bound=True,
                action_name=params.action_name,
            )
        _enforce_registered_permissions(
            form, request, params.action_name, params.object_permissions, state
        )
        denial = _enforce_object_permissions(form, request, params.action_name, state)
        if denial is not None:
            return denial
        validated = _maybe_validate_only(
            backend, request, form, params.action_name, state
        )
        if validated is not None:
            return validated
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
            resolved = current_resolver().resolve_dependencies(
                next_form.on_valid,
                request=request,
                _cache=state.dep_cache,
                _stack=state.dep_stack,
                **state.url_kwargs,
            )
            start = time.perf_counter()
            raw = next_form.on_valid(**resolved)
        else:
            resolved = current_resolver().resolve_dependencies(
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
        flashed = _flash_success_before_rerender(
            request, form, form.cleaned_data, raw, state.page_path
        )
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
        if not flashed and response.status_code < _HTTP_ERROR_FLOOR:
            _send_success_message(request, form, form.cleaned_data)
        state.emit_action_dispatched(
            request, params.action_name, form, duration_ms, response
        )
        return response

    @staticmethod
    def shape_response(
        backend: "FormActionBackend", request: "HttpRequest", outcome: ActionOutcome
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
            response["X-Next-Form"] = "invalid"
            if outcome.uid:
                response["X-Next-Action"] = outcome.uid
            return response
        if outcome.kind == ActionOutcomeKind.WIZARD_ADVANCE:
            return HttpResponseRedirect(cast("str", outcome.redirect_to))
        if outcome.form is None:
            return ensure_http_response(outcome.raw, request=request)
        return ensure_http_response(
            outcome.raw,
            request=request,
            action_name=outcome.action_name,
            backend=backend,
        )


def _branch(meta: "ActionMeta") -> "_Branch | None":
    """Return the branch `_route` takes for an action, `None` when it declares none.

    The guard reads it too, so only the form branch defers its registered permissions.
    """
    if meta.get("wizard_class") is not None:
        return "wizard"
    if meta.get("form_class") is not None:
        return "form"
    if meta.get("handler") is not None:
        return "handler"
    return None


@dataclass(slots=True)
class _DispatchState:
    """Bundle of mutable dispatch state threaded through helper methods."""

    # The pipeline submodules cannot import the facade without a cycle.
    signal_sender: ClassVar[type] = FormActionDispatch

    url_kwargs: dict[str, object]
    dep_cache: dict[str, Any]
    dep_stack: list[str]
    uid: str | None = None
    origin_match: "OriginMatch | None" = None

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
        """Send `action_dispatched` when a receiver listens for this sender."""
        sender = self.signal_sender
        if action_dispatched.has_listeners(sender):
            action_dispatched.send(
                sender=sender,
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
        self, request: "HttpRequest", action_name: str, form: "django_forms.Form"
    ) -> None:
        """Send `form_validation_failed` when a receiver listens for this sender."""
        sender = self.signal_sender
        if form_validation_failed.has_listeners(sender):
            error_count = sum(len(errors) for errors in form.errors.values())
            form_validation_failed.send(
                sender=sender,
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
        """Send `wizard_step_submitted` when a receiver listens for this sender."""
        if wizard_step_submitted.has_listeners(wizard_class):
            wizard_step_submitted.send(
                sender=wizard_class,
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
        """Send `wizard_completed` when a receiver listens for this sender."""
        if wizard_completed.has_listeners(wizard_class):
            wizard_completed.send(
                sender=wizard_class, cleaned_data=merged, uid=self.uid, request=request
            )


@dataclass(slots=True)
class _FormDispatchParams:
    """Bundle of form-specific params threaded into `_dispatch_with_form`."""

    action_name: str
    handler: "Callable[..., Any] | None"
    form_class: "type[django_forms.Form]"
    init_kwargs: dict[str, Any] = field(default_factory=dict)
    object_permissions: tuple[str, ...] = ()


__all__ = ["FormActionDispatch"]
