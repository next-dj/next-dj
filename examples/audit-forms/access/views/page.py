from access.models import AccessRequest, AuditEntry

from next import context


@context("recent_requests")
def recent_requests() -> list[AccessRequest]:
    return list(AccessRequest.objects.order_by("-created_at", "-pk")[:5])


@context("recent_audit")
def recent_audit() -> list[AuditEntry]:
    return list(AuditEntry.objects.order_by("-created_at", "-pk")[:5])
