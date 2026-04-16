"""Template tags for static asset injection slots.

``{% collect_styles %}`` emits a placeholder where CSS ``<link>`` tags will be
written after rendering. ``{% collect_scripts %}`` emits a placeholder for JS
``<script>`` tags. The placeholders are replaced by ``StaticManager.inject``
once ``Page.render`` has collected every referenced asset from the page, its
layouts, and nested components. ``{% use_style %}`` and ``{% use_script %}``
register an external URL on the active ``StaticCollector`` so that layouts and
templates can pull in shared third-party libraries without touching
``page.py`` or ``component.py`` module lists.
"""

from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

from next.static import (
    SCRIPTS_PLACEHOLDER,
    STYLES_PLACEHOLDER,
    StaticAsset,
    StaticCollector,
)


register = template.Library()


_KIND_CSS = "css"
_KIND_JS = "js"


@register.simple_tag
def collect_styles() -> str:
    """Mark where collected CSS link tags will be injected after rendering."""
    return mark_safe(STYLES_PLACEHOLDER)  # noqa: S308


@register.simple_tag
def collect_scripts() -> str:
    """Mark where collected JS script tags will be injected after rendering."""
    return mark_safe(SCRIPTS_PLACEHOLDER)  # noqa: S308


@register.simple_tag(takes_context=True)
def use_style(context: template.Context, url: str) -> str:
    """Register an external CSS URL on the active collector for later injection."""
    _register_asset(context, url, _KIND_CSS)
    return ""


@register.simple_tag(takes_context=True)
def use_script(context: template.Context, url: str) -> str:
    """Register an external JS URL on the active collector for later injection."""
    _register_asset(context, url, _KIND_JS)
    return ""


def _register_asset(context: template.Context, url: str, kind: str) -> None:
    """Prepend an asset to the render's ``StaticCollector`` when one exists in context.

    Assets declared from templates with ``{% use_style %}`` / ``{% use_script %}``
    are treated as shared third-party dependencies and are inserted before any
    co-located files or module-level lists, so the CSS cascade flows from
    generic dependencies to page-specific styling.
    """
    if not isinstance(url, str) or not url:
        return
    collector = context.get("_static_collector")
    if not isinstance(collector, StaticCollector):
        return
    collector.add(StaticAsset(url=url, kind=kind), prepend=True)
