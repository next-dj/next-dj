from datetime import timedelta

from django.utils import timezone

from access.models import AccessRequest, AuditEntry
from access.services import create_access_request


WIZARD_ACTION = "access_request_wizard"
REQUESTERS = 8
TEAMS = ("Computing", "Compilers", "Networks", "Storage")
PROJECTS = ("engine", "ledger", "atlas")


def _backend(kind: str, step: str, **fields: object) -> AuditEntry:
    """Build a row the backend channel writes for a POST of `step`."""
    return AuditEntry(
        action_name=WIZARD_ACTION,
        kind=kind,
        source=AuditEntry.SOURCE_BACKEND,
        step=step,
        **fields,
    )


def _signal(kind: str, **fields: object) -> AuditEntry:
    """Build a row a signal receiver writes, which never sees the step."""
    return AuditEntry(
        action_name=WIZARD_ACTION, kind=kind, source=AuditEntry.SOURCE_SIGNAL, **fields
    )


def _file(number: int) -> AccessRequest:
    """Store the request the requester `number` ends the wizard with."""
    return create_access_request(
        full_name=f"Requester {number:02d}",
        email=f"requester{number:02d}@example.com",
        team=TEAMS[number % len(TEAMS)],
        project_slug=PROJECTS[number % len(PROJECTS)],
        reason=f"Read access for the review number {number:02d}.",
        expires_in_days=7 + number,
    )


def _journey(number: int) -> list[list[AuditEntry]]:
    """Return the POSTs of one requester, each as the rows it leaves in both channels.

    Every third requester first fails validation and every fourth one is denied once.
    """
    posts: list[list[AuditEntry]] = []
    if number % 3 == 0:
        posts.append(
            [
                _backend(AuditEntry.KIND_REQUEST_STARTED, "identity"),
                _signal(
                    AuditEntry.KIND_VALIDATION_FAILED,
                    error_count=1,
                    field_names=["email"],
                ),
                _backend(
                    AuditEntry.KIND_DISPATCHED,
                    "identity",
                    response_status=200,
                    payload={"redirect": ""},
                ),
            ]
        )
    if number % 4 == 1:
        posts.append(
            [
                _backend(AuditEntry.KIND_REQUEST_STARTED, "scope"),
                _signal(
                    AuditEntry.KIND_ACCESS_DENIED,
                    access_layer="view",
                    access_reason="denied",
                ),
            ]
        )
    filed = _file(number)
    steps = (
        ("identity", "/request/scope/", None),
        ("scope", "/request/approval/", None),
        ("approval", f"/request/{filed.pk}/audit/?just=1", filed),
    )
    posts.extend(
        [
            _backend(AuditEntry.KIND_REQUEST_STARTED, step),
            _signal(
                AuditEntry.KIND_DISPATCHED,
                duration_ms=4.0 + number,
                response_status=302,
            ),
            _backend(
                AuditEntry.KIND_DISPATCHED,
                step,
                response_status=302,
                request=linked,
                payload={"redirect": redirect},
            ),
        ]
        for step, redirect, linked in steps
    )
    return posts


def seed_demo() -> int:
    """Fill an empty audit log with the POSTs of eight requesters and count its rows.

    The rows of one POST share a stamp, the tie the `pk` of the log ordering breaks.
    """
    if AuditEntry.objects.exists():
        return 0
    posts = [post for number in range(REQUESTERS) for post in _journey(number)]
    first = timezone.now().replace(microsecond=0) - timedelta(minutes=len(posts))
    rows: list[AuditEntry] = []
    for minute, post in enumerate(posts):
        for row in post:
            row.save()
            row.created_at = first + timedelta(minutes=minute)
            rows.append(row)
    AuditEntry.objects.bulk_update(rows, ["created_at"])
    return len(rows)
