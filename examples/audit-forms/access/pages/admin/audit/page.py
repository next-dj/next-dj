from access.models import AuditEntry
from access.services import audit_entries, audit_totals
from django.http import HttpRequest

from next import context
from next.pages import MetadataDict
from next.partial import zone_requested


metadata: MetadataDict = {"title": "Audit log", "robots": {"index": False}}

_VALID_KIND_FILTERS = frozenset(k for k, _ in AuditEntry.KIND_CHOICES)


@context("entries", zone="audit-table")
def entries(request: HttpRequest) -> list[AuditEntry] | None:
    """Load audit rows only when the lazy `audit-table` zone is rendered.

    The full page render leaves this `None` so the heavy query never
    runs behind the skeleton. The query fires only on the partial GET the
    runtime issues for the zone, where `zone_requested` is true. The
    `zone=` binding saves even the call itself on a GET for another zone.
    """
    if not zone_requested(request, "audit-table"):
        return None
    requested_kind = request.GET.get("kind", "")
    kind = requested_kind if requested_kind in _VALID_KIND_FILTERS else ""
    return audit_entries(kind=kind, limit=100)


@context("active_kind")
def active_kind(request: HttpRequest) -> str:
    return request.GET.get("kind", "")


@context("totals")
def totals() -> dict[str, int]:
    return audit_totals()
