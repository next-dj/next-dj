from dataclasses import dataclass
from datetime import datetime

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model


_ACTION_LABELS = {1: "Added", 2: "Changed", 3: "Deleted"}


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One `LogEntry` row of an object, flattened for the history page."""

    time: datetime
    user: str
    action: str
    message: str


def object_history(obj: Model) -> list[HistoryEntry]:
    """Return the `LogEntry` rows Django admin wrote for `obj`, newest first."""
    entries = (
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
        )
        .select_related("user")
        .order_by("-action_time")
    )
    return [
        HistoryEntry(
            time=entry.action_time,
            user=entry.user.get_username() if entry.user else "",
            action=_ACTION_LABELS.get(entry.action_flag, "?"),
            message=entry.get_change_message() or entry.change_message or "",
        )
        for entry in entries
    ]
