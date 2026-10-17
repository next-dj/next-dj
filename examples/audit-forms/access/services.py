from access.models import AccessRequest, AuditEntry


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


def audit_entries(*, kind: str, limit: int) -> list[AuditEntry]:
    """Return the newest `limit` audit rows, narrowed to `kind` when it names one."""
    rows = AuditEntry.objects.all()
    if kind:
        rows = rows.filter(kind=kind)
    return list(rows[:limit])


def audit_totals() -> dict[str, int]:
    """Return how many rows each audit channel wrote."""
    return {
        "backend": AuditEntry.objects.filter(source=AuditEntry.SOURCE_BACKEND).count(),
        "signal": AuditEntry.objects.filter(source=AuditEntry.SOURCE_SIGNAL).count(),
    }
