from access.models import AuditEntry
from django.http import HttpRequest

from next.pages import context


_VALID_KIND_FILTERS = frozenset(
    {
        AuditEntry.KIND_DISPATCHED,
        AuditEntry.KIND_VALIDATION_FAILED,
        AuditEntry.KIND_REQUEST_STARTED,
    },
)


@context("entries")
def entries(request: HttpRequest) -> list[AuditEntry]:
    """Return audit rows newest-first, optionally filtered by `?kind=`."""
    qs = AuditEntry.objects.all()
    requested_kind = request.GET.get("kind", "")
    if requested_kind in _VALID_KIND_FILTERS:
        qs = qs.filter(kind=requested_kind)
    return list(qs[:100])


@context("active_kind")
def active_kind(request: HttpRequest) -> str:
    return request.GET.get("kind", "")


@context("totals")
def totals() -> dict[str, int]:
    return {
        "backend": AuditEntry.objects.filter(source=AuditEntry.SOURCE_BACKEND).count(),
        "signal": AuditEntry.objects.filter(source=AuditEntry.SOURCE_SIGNAL).count(),
    }
