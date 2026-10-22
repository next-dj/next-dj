from access.models import AccessRequest

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


@context("receipt")
def receipt(access_request: DObject[AccessRequest]) -> list[tuple[str, str]]:
    return access_request.receipt_lines()


@page.metadata
def receipt_peek_meta(access_request: DObject[AccessRequest]) -> MetadataDict:
    """Retitle the tab while the receipt is open over the trail."""
    return {"title": f"Receipt preview · Request #{access_request.pk}"}
