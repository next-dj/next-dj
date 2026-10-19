from django.template import Context, Library, Node, NodeList
from django.template.base import Parser, Token

from tests.support.guards import PERM


register = Library()


class StaffOnlyNode(Node):
    """Render the body for a user holding the guard permission, nothing otherwise."""

    def __init__(self, nodelist: NodeList) -> None:
        """Hold the body the tag guards."""
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        user = getattr(context.get("request"), "user", None)
        if user is not None and user.has_perm(PERM):
            return self.nodelist.render(context)
        return ""


@register.tag
def staffonly(parser: Parser, token: Token) -> StaffOnlyNode:
    del token
    nodelist = parser.parse(("endstaffonly",))
    parser.delete_first_token()
    return StaffOnlyNode(nodelist)


@register.simple_tag(takes_context=True)
def peek(context: Context) -> str:
    """Read the render context as a third-party tag may, writing nothing."""
    del context
    return ""
