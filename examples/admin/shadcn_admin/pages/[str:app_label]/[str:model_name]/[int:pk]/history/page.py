from typing import Any

from django.db.models import Model
from shadcn_admin.services import object_history

from next import Depends, context


metadata = {"breadcrumb": "History"}


@context("history_state")
def history_state(obj: Model = Depends("admin_object")) -> dict[str, Any]:
    """Return the row the admin resolved for this URL and its `LogEntry` rows."""
    return {"obj_repr": str(obj), "rows": object_history(obj)}
