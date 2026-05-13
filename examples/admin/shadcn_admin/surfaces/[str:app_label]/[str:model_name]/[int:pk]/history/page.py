from typing import Any

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from shadcn_admin import utils

from next.pages import context


_ACTION_LABELS = {1: "Added", 2: "Changed", 3: "Deleted"}


@context("history_state")
def history_state(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int,
) -> dict[str, Any]:
    """Build the history context from `LogEntry` rows for the target object."""
    model, _model_admin, obj = utils.resolve_object_or_404(
        request, app_label, model_name, pk
    )
    ct = ContentType.objects.get_for_model(model)
    entries = (
        LogEntry.objects.filter(content_type=ct, object_id=pk)
        .select_related("user")
        .order_by("-action_time")
    )
    rows = [
        {
            "time": e.action_time,
            "user": e.user.get_username() if e.user else "",
            "action": _ACTION_LABELS.get(e.action_flag, "?"),
            "message": e.get_change_message() or e.change_message or "",
        }
        for e in entries
    ]
    return {
        "app_label": app_label,
        "model_name": model_name,
        "pk": pk,
        "obj_repr": str(obj),
        "verbose_name": str(model._meta.verbose_name),
        "rows": rows,
        "changelist_url": utils.changelist_url(app_label, model_name),
        "change_url": utils.change_url(app_label, model_name, pk),
    }
