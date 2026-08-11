"""Outcome types and response coercion for the form action dispatch pipeline."""

import warnings
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeGuard, cast

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect

from next.forms.origin import resolve_origin


if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from django import forms as django_forms
    from django.db.models import Model
    from django.http import HttpRequest

    from next.forms.backends import FormActionBackend
    from next.forms.wizard import FormWizard

    class _SuccessMessageSource(Protocol):
        """A form or wizard exposing a get_success_message hook."""

        def get_success_message(self, cleaned_data: dict[str, Any]) -> str | None: ...


_HTTP_ERROR_FLOOR = 400


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


def _is_model_instance(obj: object) -> "TypeGuard[Model]":
    """Return True when `obj` quacks like a Django model instance."""
    meta = getattr(obj, "_meta", None)
    return meta is not None and hasattr(meta, "model")


def _send_success_message(
    request: "HttpRequest", source: object, cleaned_data: dict[str, Any]
) -> None:
    """Flash the declared success message through django.contrib.messages."""
    if not hasattr(source, "get_success_message"):
        return
    message = cast("_SuccessMessageSource", source).get_success_message(cleaned_data)
    if message:
        messages.success(request, message)


def _flash_success_before_rerender(
    request: "HttpRequest",
    source: object,
    cleaned_data: dict[str, Any],
    raw: "HttpResponse | None",
    page_path: "Path | None",
) -> bool:
    """Flash the success message ahead of an in-place origin re-render."""
    # A None result re-renders the origin within this same request, so the
    # message must reach the store before that render reads {% messages %}. An
    # unresolvable origin degrades to 400 and is left to the caller status guard.
    if raw is None and page_path is not None:
        _send_success_message(request, source, cleaned_data)
        return True
    return False


def _normalize_handler_response(
    raw: "HttpResponse | str | object | None",
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


def _origin_rerender_response(
    backend: "FormActionBackend", request: "HttpRequest", action_name: str
) -> HttpResponse:
    """Re-render the origin page after a valid submission's handler returned None.

    The success response carries no invalid-submission headers and never
    re-enters `backend.shape_response`, so envelopes keyed off
    `ActionOutcomeKind.INVALID` stay untouched. An unresolvable origin
    yields 400.
    """
    origin_match = resolve_origin(request)
    if origin_match is None or origin_match.page_path is None:
        return HttpResponseBadRequest("Missing or invalid _next_form_origin")
    html = backend.render_invalid_page(
        request,
        action_name,
        None,
        origin_match.page_path,
        dict(origin_match.url_kwargs),
    )
    return HttpResponse(html)


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
            return _origin_rerender_response(backend, request, action_name)
        return HttpResponse(status=204)
    if isinstance(response, HttpResponse):
        return response
    return HttpResponse(response)


__all__ = ["ActionOutcome", "ActionOutcomeKind", "ensure_http_response"]
