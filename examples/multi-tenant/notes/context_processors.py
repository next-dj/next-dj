from __future__ import annotations

from typing import TYPE_CHECKING

from notes.access import get_active_tenant
from notes.themes import accent_color, theme_stylesheet


if TYPE_CHECKING:
    from django.http import HttpRequest


def tenant_theme(request: HttpRequest) -> dict[str, object]:
    """Surface per-tenant CSS variables and the tenant stylesheet to every page.

    Both stored columns are mapped through what the project ships rather than rendered.
    """
    tenant = get_active_tenant(request)
    if tenant is None:
        return {"tenant_theme": {}, "tenant_theme_css": "", "tenant_stylesheet": ""}
    css_vars = {"--tenant-accent": accent_color(tenant.primary_color)}
    css = ";".join(f"{name}:{value}" for name, value in css_vars.items())
    return {
        "tenant_theme": css_vars,
        "tenant_theme_css": css,
        "tenant_stylesheet": theme_stylesheet(tenant.theme),
    }
