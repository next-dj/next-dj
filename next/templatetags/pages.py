"""Template tags of the page dialect, the layout placeholder and the head tag.

The placeholder is an unnamed slot, so it takes the dialect's single and paired grammar,
and the paired body is the fallback shown where composition never reached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from django import template
from django.template.base import Node, NodeList

from next.pages.metadata.nodes import MetadataNode


if TYPE_CHECKING:
    from django.template.base import Parser, Token
    from django.template.context import Context


register = template.Library()

_END_BLOCK_TEMPLATE = ("/template",)

_SINGLE_TAKES_NO_ARGS = "{% template %} tag takes no arguments"
_PAIRED_TAKES_NO_ARGS = "{% #template %} tag takes no arguments"
_METADATA_TAKES_NO_ARGS = "{% metadata %} tag takes no arguments"


def _reject_arguments(token: Token, message: str) -> None:
    """Raise when the tag carries anything past its own name."""
    if token.split_contents()[1:]:
        raise template.TemplateSyntaxError(message)


class TemplatePlaceholderNode(Node):
    """Renders the layout fallback for a placeholder composition left unfilled."""

    def __init__(self, nodelist: NodeList) -> None:
        """Remember the fallback nodes, empty for the single form."""
        self.nodelist = nodelist

    @override
    def render(self, context: Context) -> str:
        """Render the fallback body, the empty string when the tag carries none."""
        return self.nodelist.render(context)


@register.tag(name="template")
def do_template(_parser: Parser, token: Token) -> TemplatePlaceholderNode:
    """Compile ``{% template %}``, the layout hole that carries no fallback."""
    _reject_arguments(token, _SINGLE_TAKES_NO_ARGS)
    return TemplatePlaceholderNode(nodelist=NodeList())


@register.tag(name="#template")
def do_block_template(parser: Parser, token: Token) -> TemplatePlaceholderNode:
    """Compile ``{% #template %}`` … ``{% /template %}``, whose body is the fallback."""
    _reject_arguments(token, _PAIRED_TAKES_NO_ARGS)
    nodelist = parser.parse(_END_BLOCK_TEMPLATE)
    parser.delete_first_token()
    return TemplatePlaceholderNode(nodelist=nodelist)


@register.tag(name="metadata")
def do_metadata(_parser: Parser, token: Token) -> MetadataNode:
    """Compile ``{% metadata %}``, the head tags of the page being rendered."""
    _reject_arguments(token, _METADATA_TAKES_NO_ARGS)
    return MetadataNode()
