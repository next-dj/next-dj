from access.models import AuditEntry
from django.http import HttpRequest

from next.pages import context
from next.partial import zone_requested


_VALID_KIND_FILTERS = frozenset(k for k, _ in AuditEntry.KIND_CHOICES)


@context("entries")
def entries(request: HttpRequest) -> list[AuditEntry] | None:
    """Load audit rows only when the lazy `audit-table` zone is rendered.

    The full page render leaves this `None` so the heavy query never
    runs behind the skeleton. The query fires only on the partial GET the
    runtime issues for the zone, where `zone_requested` is true.
    """
    if not zone_requested(request, "audit-table"):
        return None
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
