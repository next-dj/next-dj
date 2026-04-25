from __future__ import annotations

from typing import TYPE_CHECKING, Any

from access.models import AuditEntry
from next.forms import RegistryFormActionBackend


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


_RESERVED_FORM_KEYS = frozenset(
    {
        "csrfmiddlewaretoken",
        "_next_form_uid",
        "_next_form_page",
    },
)


def _safe_form_payload(request: HttpRequest) -> dict[str, list[str]]:
    """Capture POST keys for the audit row, dropping framework-internal fields.

    Multi-value fields (checkbox groups, multi-selects) preserve every
    submitted value via `QueryDict.lists()`. Production projects should
    extend `_RESERVED_FORM_KEYS` with any password / secret field names
    they use, since this payload is persisted to the audit log.
    """
    return {
        key: values
        for key, values in request.POST.lists()
        if key not in _RESERVED_FORM_KEYS and not key.startswith("_url_param_")
    }


class AuditedFormActionBackend(RegistryFormActionBackend):
    """Registry backend that writes a backend-sourced `AuditEntry` per dispatch.

    Records two rows per request: `request_started` with the captured form
    payload, and `dispatched` with the resolved response status. The signal
    receivers in `access.receivers` write a parallel row from
    `action_dispatched` and `form_validation_failed`, so the admin page can
    show both channels side by side.
    """

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Wrap the registry dispatch with two backend-sourced audit rows.

        Skips auditing for UIDs that the registry does not know — those
        return 404 from `super().dispatch` and have no associated action.
        """
        if uid not in self._uid_to_name:
            return super().dispatch(request, uid)
        action_name = self._uid_to_name[uid]
        step = request.POST.get("step", "") if request.method == "POST" else ""
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
        AuditEntry.objects.create(
            action_name=action_name,
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
            step=step,
            response_status=response.status_code,
            payload={"redirect": response.get("Location", "")},
        )
        return response
