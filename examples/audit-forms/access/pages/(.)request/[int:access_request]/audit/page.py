from access.models import AccessRequest, AuditEntry

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


@context("entries", zone="audit-peek-entries")
def entries(access_request: DObject[AccessRequest]) -> list[AuditEntry]:
    """Return the rows linked to the request, the one query a poll of the peek runs."""
    return list(access_request.audit_entries.order_by("-created_at", "-pk"))


@page.metadata
def peek_meta(access_request: DObject[AccessRequest]) -> MetadataDict:
    """Retitle the tab while the peek is open, the layer restores the host on close."""
    return {"title": f"Audit peek · Request #{access_request.pk}"}
