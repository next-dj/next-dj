from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from django.http import HttpRequest

    from notes.models import Tenant


def get_active_tenant(request: HttpRequest) -> Tenant | None:
    """Return the tenant attached to `request` by `TenantMiddleware`.

    The middleware stashes the resolved tenant under `request.tenant`.
    Every Python consumer that needs the active tenant should go
    through this helper rather than reading the attribute directly.
    """
    return getattr(request, "tenant", None)
