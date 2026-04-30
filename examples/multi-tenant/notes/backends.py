from __future__ import annotations

from typing import TYPE_CHECKING

from next.static import StaticFilesBackend
from notes.access import get_active_tenant


if TYPE_CHECKING:
    from django.http import HttpRequest


PREFIX_FORMAT = "/_t/{slug}"


class TenantPrefixStaticBackend(StaticFilesBackend):
    """Prepend a per-tenant URL prefix to every collected asset URL.

    The tenant is read from `request.tenant`, attached upstream by
    `TenantMiddleware`. The original URL is preserved when no tenant
    is in scope, which keeps offline tag rendering and management
    commands behaving the same way as the default backend.
    """

    def render_link_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,
    ) -> str:
        """Return a CSS link tag with the per-tenant prefix injected."""
        return super().render_link_tag(_prefixed(url, request))

    def render_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,
    ) -> str:
        """Return a JS script tag with the per-tenant prefix injected."""
        return super().render_script_tag(_prefixed(url, request))


def _prefixed(url: str, request: HttpRequest | None) -> str:
    """Return `url` with `/_t/<slug>` injected before its path."""
    tenant = get_active_tenant(request) if request is not None else None
    if tenant is None or not url.startswith("/"):
        return url
    return PREFIX_FORMAT.format(slug=tenant.slug) + url
