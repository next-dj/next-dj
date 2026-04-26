from __future__ import annotations

from django.dispatch import receiver

from next.forms.signals import action_dispatched, form_validation_failed

from .models import AuditEntry


@receiver(action_dispatched)
def _on_action_dispatched(
    action_name: str,
    duration_ms: float,
    response_status: int,
    **_: object,
) -> None:
    """Record one signal-sourced row per successful dispatch.

    Mirrors the backend channel but keeps no request payload, only the
    timing and HTTP status carried by the signal.
    """
    AuditEntry.objects.create(
        action_name=action_name,
        kind=AuditEntry.KIND_DISPATCHED,
        source=AuditEntry.SOURCE_SIGNAL,
        duration_ms=duration_ms,
        response_status=response_status,
    )


@receiver(form_validation_failed)
def _on_form_validation_failed(
    action_name: str,
    error_count: int,
    field_names: tuple[str, ...],
    **_: object,
) -> None:
    """Record one signal-sourced row per validation failure."""
    AuditEntry.objects.create(
        action_name=action_name,
        kind=AuditEntry.KIND_VALIDATION_FAILED,
        source=AuditEntry.SOURCE_SIGNAL,
        error_count=error_count,
        field_names=list(field_names),
    )
