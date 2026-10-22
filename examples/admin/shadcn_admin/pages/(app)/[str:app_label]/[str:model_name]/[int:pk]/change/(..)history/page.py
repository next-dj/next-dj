from typing import Any

from django.db.models import Model
from shadcn_admin.services import object_history

from next import Depends, context, page
from next.pages import MetadataDict


PEEK_ROWS = 5


@context("history_peek")
def history_peek(obj: Model = Depends("admin_object")) -> dict[str, Any]:
    """Return the latest `LogEntry` rows of the row, the full log one link away."""
    rows = object_history(obj)
    return {"rows": rows[:PEEK_ROWS], "hidden": max(len(rows) - PEEK_ROWS, 0)}


@page.metadata
def history_peek_meta(obj: Model = Depends("admin_object")) -> MetadataDict:
    """Title the tab after the row while its history covers the editor."""
    return {"title": f"History · {obj}"}
