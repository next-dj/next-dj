from access.models import AccessRequest, AuditEntry
from django.http import Http404, HttpRequest

from next.pages import context


@context("access_request")
def access_request(request_id: int) -> AccessRequest:
    """Resolve the `AccessRequest` for the URL kwarg or 404."""
    try:
        return AccessRequest.objects.get(pk=request_id)
    except AccessRequest.DoesNotExist as exc:
        raise Http404 from exc


@context("entries")
def entries(request_id: int) -> list[AuditEntry]:
    """Return audit rows linked to this request, newest-first."""
    return list(AuditEntry.objects.filter(request_id=request_id))


@context("just_submitted")
def just_submitted(request: HttpRequest) -> bool:
    return request.GET.get("just") == "1"
