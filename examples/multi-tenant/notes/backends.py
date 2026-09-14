from __future__ import annotations

from typing import TYPE_CHECKING

from next.static import StaticFilesBackend
from notes.access import get_active_tenant


if TYPE_CHECKING:
    from django.http import HttpRequest


PREFIX_FORMAT = "/_t/{slug}"


class TenantPrefixStaticBackend(StaticFilesBackend):
    """Prepend a per-tenant URL prefix to every asset URL the pipeline renders.

    The tenant comes from `request.tenant`, attached upstream by `TenantMiddleware`, and
    rewriting in `asset_url` covers every asset kind from one place.
    """

    def asset_url(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return `url` with `/_t/<slug>` injected before its path."""
        tenant = get_active_tenant(request) if request is not None else None
        if tenant is None or not url.startswith("/"):
            return url
        return PREFIX_FORMAT.format(slug=tenant.slug) + url
