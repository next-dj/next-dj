from __future__ import annotations

from typing import TYPE_CHECKING

from notes.access import get_active_tenant


if TYPE_CHECKING:
    from django.http import HttpRequest


def tenant_theme(request: HttpRequest) -> dict[str, object]:
    """Surface per-tenant CSS variables to every page template.

    The middleware attaches the active `Tenant` to `request.tenant`.
    On error pages where the middleware short-circuited, the attribute
    is missing and we return an empty dict so templates can render
    unbranded fallbacks.
    """
    tenant = get_active_tenant(request)
    if tenant is None:
        return {"tenant_theme": {}, "tenant_theme_css": ""}
    css_vars = {"--tenant-accent": tenant.primary_color}
    css = ";".join(f"{name}:{value}" for name, value in css_vars.items())
    return {"tenant_theme": css_vars, "tenant_theme_css": css}
