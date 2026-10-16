"""Template tags that branch on the permissions of the user a page renders for."""

from typing import Final

from django import template
from django.template.base import NodeList, Parser, Token

from next.permissions.nodes import HasPermNode


register = template.Library()

_ELSE: Final = "else"
_END: Final = "endhas_perm"
_BARE: Final = 2
_WITH_OBJECT: Final = 3


@register.tag(name="has_perm")
def do_has_perm(parser: Parser, token: Token) -> HasPermNode:
    """Compile `{% has_perm perm obj %}` … `{% else %}` … `{% endhas_perm %}`."""
    bits = token.split_contents()
    if len(bits) not in {_BARE, _WITH_OBJECT}:
        msg = f"{bits[0]} takes a permission and an optional object"
        raise template.TemplateSyntaxError(msg)
    perm = parser.compile_filter(bits[1])
    obj = parser.compile_filter(bits[2]) if len(bits) == _WITH_OBJECT else None
    granted = parser.parse((_ELSE, _END))
    denied = NodeList()
    if parser.next_token().contents == _ELSE:
        denied = parser.parse((_END,))
        parser.delete_first_token()
    return HasPermNode(perm, obj, granted, denied)


__all__ = ["register"]
