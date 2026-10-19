from __future__ import annotations

from functools import partial

from django.db import transaction
from django.dispatch import receiver

from next.forms.signals import (
    action_dispatched,
    form_access_denied,
    form_validation_failed,
)

from .models import AuditEntry


@receiver(action_dispatched)
def _on_action_dispatched(
    action_name: str, duration_ms: float, response_status: int, **kwargs
) -> None:
    """Record one signal-sourced row per successful dispatch, once its writes commit.

    Unlike the backend channel it keeps no payload, only the signal's timing and status.
    """
    transaction.on_commit(
        partial(
            AuditEntry.objects.create,
            action_name=action_name,
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_SIGNAL,
            duration_ms=duration_ms,
            response_status=response_status,
        )
    )


@receiver(form_validation_failed)
def _on_form_validation_failed(
    action_name: str, error_count: int, field_names: tuple[str, ...], **kwargs
) -> None:
    """Record one signal-sourced row per validation failure, once its writes commit."""
    transaction.on_commit(
        partial(
            AuditEntry.objects.create,
            action_name=action_name,
            kind=AuditEntry.KIND_VALIDATION_FAILED,
            source=AuditEntry.SOURCE_SIGNAL,
            error_count=error_count,
            field_names=list(field_names),
        )
    )


@receiver(form_access_denied)
def _on_form_access_denied(action_name: str, layer: str, reason: str, **kwargs) -> None:
    """Record one signal-sourced row per denial of a dynamic permission hook.

    The denied step writes no draft, so this row is the only trace of the attempt.
    """
    transaction.on_commit(
        partial(
            AuditEntry.objects.create,
            action_name=action_name,
            kind=AuditEntry.KIND_ACCESS_DENIED,
            source=AuditEntry.SOURCE_SIGNAL,
            access_layer=layer,
            access_reason=reason,
        )
    )
