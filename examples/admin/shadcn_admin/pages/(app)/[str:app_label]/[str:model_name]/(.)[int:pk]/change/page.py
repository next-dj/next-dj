from django.db.models import Model

from next import Depends, page
from next.pages import MetadataDict


@page.metadata
def quick_edit_meta(obj: Model = Depends("admin_object")) -> MetadataDict:
    """Title the tab after the row while the quick editor covers the changelist."""
    return {"title": f"Quick edit · {obj}"}
