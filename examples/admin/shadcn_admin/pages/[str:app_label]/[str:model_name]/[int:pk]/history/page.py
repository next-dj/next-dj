from typing import Any

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpRequest
from shadcn_admin.utils import resolve_model_admin

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
    model, model_admin = resolve_model_admin(app_label, model_name)
    obj = model_admin.get_object(request, pk)
    if obj is None:
        raise Http404
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
        "changelist_url": f"/admin/{app_label}/{model_name}/",
        "change_url": f"/admin/{app_label}/{model_name}/{pk}/change/",
    }
