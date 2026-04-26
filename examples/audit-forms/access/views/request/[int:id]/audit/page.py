from access.models import AccessRequest, AuditEntry
from django.http import Http404, HttpRequest

from next.pages import context


@context("access_request")
def access_request(id: int) -> AccessRequest:  # noqa: A002
    """Resolve the `AccessRequest` for the URL kwarg or 404."""
    try:
        return AccessRequest.objects.get(pk=id)
    except AccessRequest.DoesNotExist as exc:
        raise Http404 from exc


@context("entries")
def entries(id: int) -> list[AuditEntry]:  # noqa: A002
    """Return audit rows linked to this request, newest-first."""
    return list(AuditEntry.objects.filter(request_id=id))


@context("just_submitted")
def just_submitted(request: HttpRequest) -> bool:
    return request.GET.get("just") == "1"
