from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.urls import Resolver404, resolve

from access.forms import POLICY_FIELD
from access.models import AuditEntry
from next.forms import RegistryFormActionBackend


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


_RESERVED_FORM_KEYS = frozenset(
    {"csrfmiddlewaretoken", "_next_form_origin", POLICY_FIELD}
)


def _safe_form_payload(request: HttpRequest) -> dict[str, list[str]]:
    """Capture POST keys for the audit row, dropping framework-internal fields.

    A real project adds its secret field names to `_RESERVED_FORM_KEYS`.
    """
    return {
        key: values
        for key, values in request.POST.lists()
        if key not in _RESERVED_FORM_KEYS
    }


def _step_from_origin(request: HttpRequest) -> str:
    """Recover the active wizard step by resolving the posted origin URL."""
    origin = request.POST.get("_next_form_origin", "")
    path = origin.partition("?")[0]
    if not path.startswith("/"):
        return ""
    try:
        match = resolve(path)
    except Resolver404:
        return ""
    return str(match.kwargs.get("step", ""))


class AuditedFormActionBackend(RegistryFormActionBackend):
    """Registry backend that writes backend-sourced `AuditEntry` rows per dispatch."""

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Wrap the registry dispatch with `request_started` and `dispatched` rows."""
        action_name = self.action_name(uid)
        if action_name is None:
            return super().dispatch(request, uid)
        step = _step_from_origin(request) if request.method == "POST" else ""
        payload: dict[str, Any] = (
            _safe_form_payload(request) if request.method == "POST" else {}
        )
        AuditEntry.objects.create(
            action_name=action_name,
            kind=AuditEntry.KIND_REQUEST_STARTED,
            source=AuditEntry.SOURCE_BACKEND,
            step=step,
            payload=payload,
        )
        response = super().dispatch(request, uid)
        request_id = _consume_just_created(request)
        AuditEntry.objects.create(
            action_name=action_name,
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
            request_id=request_id,
            step=step,
            response_status=response.status_code,
            payload={"redirect": response.get("Location", "")},
        )
        return response


def _consume_just_created(request: HttpRequest) -> int | None:
    """Pop the per-request correlation id stored by the form handler."""
    raw = request.session.pop("access_request_just_created", None)
    if raw is None:
        return None
    request.session.modified = True
    return int(raw)
