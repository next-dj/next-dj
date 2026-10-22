from access.models import AccessRequest, AuditEntry
from django.http import HttpRequest

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


@context("access_request")
def access_request(access_request: DObject[AccessRequest]) -> AccessRequest:
    """Expose the request the URL names, which `visible_to` loads or answers 404."""
    return access_request


@page.metadata
def audit_meta(access_request: DObject[AccessRequest]) -> MetadataDict:
    """Title the trail after the request number, never after the requester."""
    return {"title": f"Request #{access_request.pk}", "robots": {"index": False}}


@context("entries")
def entries(access_request: DObject[AccessRequest]) -> list[AuditEntry]:
    """Return audit rows linked to this request, newest-first."""
    return list(access_request.audit_entries.all())


@context("just_submitted")
def just_submitted(request: HttpRequest) -> bool:
    return request.GET.get("just") == "1"


@context("filed")
def filed(access_request: DObject[AccessRequest]) -> bool:
    """Offer the receipt only once the backend recorded the filing dispatch."""
    return access_request.is_filed()
