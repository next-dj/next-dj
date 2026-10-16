from typing import Any

from admin_audit.models import AdminActivityLog
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from next import context


_LIMIT = 50

metadata = {"breadcrumb": "Activity"}


@context("activity_state")
def activity_state(request: HttpRequest) -> dict[str, Any]:
    """Return the latest activity entries, refusing staff without the view right."""
    if not request.user.has_perm("admin_audit.view_adminactivitylog"):
        raise PermissionDenied
    entries = AdminActivityLog.objects.select_related("user").all()[:_LIMIT]
    rows = [
        {
            "timestamp": entry.timestamp,
            "user": entry.user.get_username() if entry.user else "",
            "action": entry.action,
            "target": (
                f"{entry.app_label}.{entry.model_name}" if entry.app_label else ""
            ),
            "object_repr": entry.object_repr,
            "status": entry.response_status,
        }
        for entry in entries
    ]
    return {"rows": rows, "limit": _LIMIT}
