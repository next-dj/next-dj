from typing import Any

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model


_ACTION_LABELS = {1: "Added", 2: "Changed", 3: "Deleted"}


def object_history(obj: Model) -> list[dict[str, Any]]:
    """Return the `LogEntry` rows Django admin wrote for `obj`, newest first."""
    entries = (
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
        )
        .select_related("user")
        .order_by("-action_time")
    )
    return [
        {
            "time": entry.action_time,
            "user": entry.user.get_username() if entry.user else "",
            "action": _ACTION_LABELS.get(entry.action_flag, "?"),
            "message": entry.get_change_message() or entry.change_message or "",
        }
        for entry in entries
    ]
