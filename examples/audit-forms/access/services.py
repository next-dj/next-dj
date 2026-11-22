from django.db.models import Count, Q

from access.models import AccessRequest, AuditEntry, AuditEntryQuerySet


def create_access_request(
    *,
    full_name: str,
    email: str,
    team: str,
    project_slug: str,
    reason: str,
    expires_in_days: int,
) -> AccessRequest:
    """Store a request the wizard collected, pending until someone reviews it."""
    return AccessRequest.objects.create(
        full_name=full_name,
        email=email,
        team=team,
        project_slug=project_slug,
        reason=reason,
        expires_in_days=expires_in_days,
    )


def recent_audit(limit: int) -> list[AuditEntry]:
    """Return the newest `limit` audit rows, the later of two equal stamps first."""
    return list(AuditEntry.objects.order_by("-created_at", "-pk")[:limit])


def audit_entries(*, kind: str) -> AuditEntryQuerySet:
    """Return the audit rows of `kind`, lazy so a pager reads one window of them."""
    return AuditEntry.objects.of_kind(kind)


def audit_totals(*, kind: str) -> dict[str, int]:
    """Return how many rows of `kind` each audit channel wrote, in one query."""
    return AuditEntry.objects.of_kind(kind).aggregate(
        backend=Count("pk", filter=Q(source=AuditEntry.SOURCE_BACKEND)),
        signal=Count("pk", filter=Q(source=AuditEntry.SOURCE_SIGNAL)),
    )
