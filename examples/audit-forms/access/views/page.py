from __future__ import annotations

from access.models import AccessRequest, AuditEntry

from next.pages import context


@context("recent_requests")
def recent_requests() -> list[AccessRequest]:
    return list(AccessRequest.objects.all()[:5])


@context("recent_audit")
def recent_audit() -> list[AuditEntry]:
    return list(AuditEntry.objects.all()[:5])
