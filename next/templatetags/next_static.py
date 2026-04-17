"""Template tags for static asset injection slots.

``{% collect_styles %}`` emits a placeholder where CSS ``<link>`` tags will be
written after rendering. ``{% collect_scripts %}`` emits a placeholder for JS
``<script>`` tags. The placeholders are replaced by ``StaticManager.inject``
once ``Page.render`` has collected every referenced asset from the page, its
layouts, and nested components. ``{% use_style %}`` and ``{% use_script %}``
register an external URL on the active ``StaticCollector`` so that layouts and
templates can pull in shared third-party libraries without touching
``page.py`` or ``component.py`` module lists. ``{% #use_style %}`` and
``{% #use_script %}`` are block forms whose body is rendered with the current
context and hoisted into the matching slot, so developers can co-locate
inline ``<style>`` or ``<script>`` blocks with their components while still
letting the collector control final placement and order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import template
from django.template.base import Node, NodeList
from django.utils.safestring import mark_safe

from next.static import (
    _KIND_CSS,
    _KIND_JS,
    SCRIPTS_PLACEHOLDER,
    STYLES_PLACEHOLDER,
    StaticAsset,
    StaticCollector,
)


if TYPE_CHECKING:
    from django.template.base import Parser, Token


register = template.Library()

_END_BLOCK_USE_STYLE = ("/use_style",)
_END_BLOCK_USE_SCRIPT = ("/use_script",)


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


class _InlineAssetNode(Node):
    """Render an inline asset body and push it onto the active collector.

    The block body is rendered with the current template context so inline
    scripts and styles can still interpolate page variables. The rendered
    body is stripped of leading and trailing whitespace before it reaches
    the collector, so blank-only blocks are silently ignored. The collector
    dedupes inline entries by the stripped body itself, so two blocks that
    produce identical HTML collapse to one entry, while blocks that
    interpolate different values into the body stay distinct. The node emits
    nothing in place because the collector controls final placement inside
    the matching collect_styles or collect_scripts slot.
    """

    def __init__(self, kind: str, nodelist: NodeList) -> None:
        """Remember the asset kind and the nested nodes to render at runtime."""
        self.kind = kind
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Render the body, register the HTML on the collector, and emit nothing."""
        collector = context.get("_static_collector")
        if not isinstance(collector, StaticCollector):
            return ""
        body = self.nodelist.render(context)
        stripped = body.strip()
        if not stripped:
            return ""
        collector.add(StaticAsset(url="", kind=self.kind, inline=stripped))
        return ""


@register.tag(name="#use_style")
def do_block_use_style(parser: Parser, _token: Token) -> _InlineAssetNode:
    """Compile ``{% #use_style %}`` … ``{% /use_style %}`` into an inline CSS block."""
    nodelist = parser.parse(_END_BLOCK_USE_STYLE)
    parser.delete_first_token()
    return _InlineAssetNode(kind=_KIND_CSS, nodelist=nodelist)


@register.tag(name="#use_script")
def do_block_use_script(parser: Parser, _token: Token) -> _InlineAssetNode:
    """Compile ``{% #use_script %}`` … ``{% /use_script %}`` into an inline JS block."""
    nodelist = parser.parse(_END_BLOCK_USE_SCRIPT)
    parser.delete_first_token()
    return _InlineAssetNode(kind=_KIND_JS, nodelist=nodelist)
