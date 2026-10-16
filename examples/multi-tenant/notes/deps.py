from django.http import HttpRequest

from next.deps import resolver
from notes.access import get_active_tenant
from notes.models import Tenant


@resolver.dependency("scope")
def scope(request: HttpRequest) -> Tenant | None:
    """Return the tenant of the request, which narrows every note lookup."""
    return get_active_tenant(request)
