"""Template tags for third-party scripts and consent-gated markup.

`script` notes the render's collector, and `consented` gates its body.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, cast, override

from django import template
from django.conf import settings
from django.template.base import NodeList
from django.utils.html import format_html
from django.utils.safestring import SafeString

from next.consent import consent_categories, get_consent
from next.consent.manager import consent_configured, server_mode
from next.diagnostics import FailureLog
from next.pages.responses import vary_on_cookie
from next.scripts.manager import CONSENT_NOTE, GATED_NOTE, SCRIPT_NOTE, GatedNote
from next.scripts.nodes import ConsentedTagNode
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static import StaticCollector, default_placeholders, get_static_manager


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.template.base import FilterExpression, Parser, Token


register = template.Library()

logger = logging.getLogger("next.scripts")

_failures = FailureLog(logger)

_END_CONSENTED: Final = ("/consented",)
_ELSE: Final = "else"
_CONSENTED_BITS: Final = 2
_CONSENTED_END: Final = SafeString("<!--/next-consented-->")
_STYLES_SLOT: Final = "styles"
_HELD_KINDS: Final = frozenset({"js", "module"})


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


def _warn_unknown(category: str) -> None:
    """Log once under `DEBUG` a category the configured list leaves out."""
    if settings.DEBUG and consent_configured() and category not in consent_categories():
        _failures.warn(
            ("category", category),
            "{%% #consented %r %%} names a category "
            "NEXT_FRAMEWORK['CONSENT']['CATEGORIES'] does not list, so no visitor "
            "can grant it and the block renders its else branch. Add it to the list.",
            category,
        )


def _merge_held(
    shadow: StaticCollector, collector: StaticCollector, category: str
) -> None:
    """Merge what a client-rendered gated body registered into the render's collector.

    Styles and the JS context pass, being inert until the markup they serve shows.
    A script waits in the manifest for the category, and any other kind is dropped.
    """
    for slot in default_placeholders:
        for asset in shadow.assets_in_slot(slot.name):
            if slot.name == _STYLES_SLOT:
                collector.add(asset)
            elif asset.kind in _HELD_KINDS:
                collector.note(GATED_NOTE, GatedNote(category, asset))
            else:
                _failures.warn(
                    ("kind", asset.kind),
                    "{%% #consented %%} drops a %r asset its body registers on a "
                    "page the runtime renders consent for, since only scripts can "
                    "wait for the category. Register it outside the block.",
                    asset.kind,
                )
    serializers = shadow.js_context_serializers()
    for key, value in shadow.js_context().items():
        collector.add_js_context(key, value, serializer=serializers.get(key))
    for name in shadow.notes(SCRIPT_NOTE):
        collector.note(GATED_NOTE, GatedNote(category, cast("str", name)))
    for note in shadow.notes(GATED_NOTE):
        collector.note(GATED_NOTE, note)
    for note in shadow.notes(CONSENT_NOTE):
        collector.note(CONSENT_NOTE, note)


class ConsentedNode(ConsentedTagNode):
    """Renders its body for a visitor who granted the category, else its else branch.

    A client-rendered page gets both, the body inert in a template, then an end marker.
    The body then registers on a collector of its own, so the scripts it names wait
    for the category in the manifest instead of loading with the page.
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
        _warn_unknown(category)
        request = _request(context)
        collector = _collector(context)
        if collector is not None:
            collector.note(CONSENT_NOTE, category)
        if not server_mode(request):
            return format_html(
                '<template data-next-consented="{}">{}</template>{}{}',
                category,
                SafeString(self._render_held(context, collector, category)),
                SafeString(self.denied.render(context)),
                _CONSENTED_END,
            )
        vary_on_cookie(request)
        branch = self.granted if get_consent(request).allows(category) else self.denied
        return branch.render(context)

    def _render_held(
        self,
        context: template.Context,
        collector: StaticCollector | None,
        category: str,
    ) -> str:
        """Render the body against a collector of its own, then merge what may pass."""
        if collector is None:
            return self.granted.render(context)
        shadow = get_static_manager().create_collector()
        with context.push({COLLECTOR_KEY: shadow}):
            body = self.granted.render(context)
        _merge_held(shadow, collector, category)
        return body


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
