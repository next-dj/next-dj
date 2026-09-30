"""Template tags for third-party scripts and consent-gated markup.

`script` notes the render's collector, and `consented` gates its body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast, override

from django import template
from django.template.base import NodeList
from django.utils.html import format_html
from django.utils.safestring import SafeString

from next.consent import get_consent
from next.consent.manager import server_mode
from next.pages.responses import vary_on_cookie
from next.scripts.manager import CONSENT_NOTE, SCRIPT_NOTE
from next.scripts.nodes import ConsentedTagNode
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static import StaticCollector


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.template.base import FilterExpression, Parser, Token


register = template.Library()

_END_CONSENTED: Final = ("/consented",)
_ELSE: Final = "else"
_CONSENTED_BITS: Final = 2
_CONSENTED_END: Final = SafeString("<!--/next-consented-->")


def _collector(context: template.Context) -> StaticCollector | None:
    collector = context.get(COLLECTOR_KEY)
    return collector if isinstance(collector, StaticCollector) else None


def _request(context: template.Context) -> HttpRequest | None:
    return cast("HttpRequest | None", context.get(REQUEST_KEY))


@register.simple_tag(takes_context=True)
def script(context: template.Context, name: str) -> str:
    """Render the script `name` the tree declares with `auto=False` on this page."""
    collector = _collector(context)
    if collector is not None and isinstance(name, str) and name:
        collector.note(SCRIPT_NOTE, name)
    return ""


class ConsentedNode(ConsentedTagNode):
    """Renders its body for a visitor who granted the category, else its else branch.

    A client-rendered page gets both, the body inert in a template, then an end marker.
    """

    def __init__(
        self, category: FilterExpression, granted: NodeList, denied: NodeList
    ) -> None:
        """Store the category expression and both branches."""
        self.category = category
        self.granted = granted
        self.denied = denied

    @override
    def render(self, context: template.Context) -> str:
        """Render the branch the consent of the visitor picks."""
        category = str(self.category.resolve(context))
        request = _request(context)
        collector = _collector(context)
        if collector is not None:
            collector.note(CONSENT_NOTE, category)
        if not server_mode(request):
            return format_html(
                '<template data-next-consented="{}">{}</template>{}{}',
                category,
                SafeString(self.granted.render(context)),
                SafeString(self.denied.render(context)),
                _CONSENTED_END,
            )
        vary_on_cookie(request)
        branch = self.granted if get_consent(request).allows(category) else self.denied
        return branch.render(context)


@register.tag(name="#consented")
def do_consented(parser: Parser, token: Token) -> ConsentedNode:
    """Compile `{% #consented "category" %}` … `{% else %}` … `{% /consented %}`."""
    bits = token.split_contents()
    if len(bits) != _CONSENTED_BITS:
        msg = f"{bits[0]} takes exactly one category"
        raise template.TemplateSyntaxError(msg)
    category = parser.compile_filter(bits[1])
    granted = parser.parse((_ELSE, *_END_CONSENTED))
    denied = NodeList()
    if parser.next_token().contents == _ELSE:
        denied = parser.parse(_END_CONSENTED)
        parser.delete_first_token()
    return ConsentedNode(category, granted, denied)
