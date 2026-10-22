from access.models import AccessRequest

from next import context
from next.deps import DObject
from next.pages import MetadataDict


metadata: MetadataDict = {"robots": {"index": False}}


@context("access_request", inherit_context=True)
def access_request(access_request: DObject[AccessRequest]) -> AccessRequest:
    """Expose the request the target URL names, `visible_to` loads it or answers 404."""
    return access_request
