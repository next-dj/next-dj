"""Template tags for static asset injection slots.

The collect tags emit placeholder tokens and the use tags register assets on
the request's ``StaticCollector``, so ``StaticManager.inject`` controls the
final markup once ``Page.render`` has collected every referenced asset.
Custom kinds register through the same public ``KindRegistry.register`` API
without needing new template tags.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from django import template
from django.template.base import Node, NodeList
from django.utils.safestring import SafeString

from next.static import StaticAsset, StaticCollector, default_placeholders


if TYPE_CHECKING:
    from django.template.base import Parser, Token


register = template.Library()


_KIND_CSS = "css"
_KIND_JS = "js"
_KIND_MODULE = "module"
_END_BLOCK_USE_STYLE = ("/use_style",)
_END_BLOCK_USE_SCRIPT = ("/use_script",)


def _slot_token(name: str) -> str:
    slot = default_placeholders.get(name)
    return slot.token if slot is not None else ""


@register.simple_tag
def collect_styles() -> SafeString:
    """Mark where collected CSS link tags will be injected after rendering."""
    return SafeString(_slot_token("styles"))


@register.simple_tag
def collect_scripts() -> SafeString:
    """Mark where collected JS script tags will be injected after rendering."""
    return SafeString(_slot_token("scripts"))


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


@register.simple_tag(takes_context=True)
def use_module(context: template.Context, url: str) -> str:
    """Register an external ES module URL on the active collector for injection."""
    _register_asset(context, url, _KIND_MODULE)
    return ""


def _register_asset(context: template.Context, url: str, kind: str) -> None:
    """Prepend an asset to the render's ``StaticCollector`` when one exists in context.

    URL-tag assets are shared third-party dependencies, so they land before
    co-located files and module-level lists and the CSS cascade flows from
    generic to page-specific.
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

    @override
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
