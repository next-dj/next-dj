from typing import Annotated

from access.models import AuditEntry
from access.services import audit_entries
from django.http import HttpRequest

from next import context
from next.pages import MetadataDict
from next.partial import KeysetPager, Paging, Window


metadata: MetadataDict = {"title": "Audit log", "robots": {"index": False}}

log = KeysetPager(("-created_at", "-pk"), per_page=20, max_restore=3)


@context("active_kind")
def active_kind(request: HttpRequest) -> str:
    return request.GET.get("kind", "")


@context("entries")
def entries(active_kind: str, page: Annotated[Paging, log]) -> Window[AuditEntry]:
    """Return the window of the log the cursor names, the newest rows first."""
    return page.paginate(audit_entries(kind=active_kind))
