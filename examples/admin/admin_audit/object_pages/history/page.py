from typing import Any

from admin_audit.services import object_history
from django.db.models import Model
from django.utils.text import capfirst

from next import Depends, context, page
from next.pages import MetadataDict


@context("history_state")
def history_state(obj: Model = Depends("admin_object")) -> dict[str, Any]:
    """Return the row the admin resolved for this URL and its `LogEntry` rows."""
    return {"obj_repr": str(obj), "rows": object_history(obj)}


@page.metadata
def history_meta(obj: Model = Depends("admin_object")) -> MetadataDict:
    """Title the page after the model of the row, as the changelist tabs read."""
    return {
        "title": capfirst(str(obj._meta.verbose_name_plural)),
        "breadcrumb": "History",
    }
