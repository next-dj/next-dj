from access.models import AccessRequest

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


@context("access_request")
def access_request(access_request: DObject[AccessRequest]) -> AccessRequest:
    """Expose the request an audit row links to, loaded from the target URL kwargs."""
    return access_request


@context("linked_rows")
def linked_rows(access_request: DObject[AccessRequest]) -> int:
    return access_request.audit_entries.count()


@page.metadata
def log_peek_meta(access_request: DObject[AccessRequest]) -> MetadataDict:
    """Retitle the tab after the request a log row points at."""
    return {
        "title": f"Request #{access_request.pk} from the log",
        "robots": {"index": False},
    }
