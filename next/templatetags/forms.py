"""Template tag library exposing `{% form %}` and `{% action_url %}` to Django."""

from typing import TYPE_CHECKING

from django import template

from next.forms.errors import FormActionNotFoundError
from next.forms.manager import form_action_manager
from next.forms.nodes import FormNode, anchor_lookup_from_context
from next.partial.keys import FORM_KEY_ATTR, FORM_ZONE_ATTR


if TYPE_CHECKING:
    from django.template.base import FilterExpression


_MIN_FORM_TAG_BITS = 2
_RESERVED_FORM_ATTRS = frozenset({"action", "method"})
_RESERVED_FORM_ATTR_PREFIX = "data-next-"

_PARTIAL_FORM_PARAMS: dict[str, str] = {
    "validate": "data-next-validate",
    "trigger": "data-next-trigger",
    "debounce": "data-next-debounce",
    "zone": FORM_ZONE_ATTR,
    "key": FORM_KEY_ATTR,
}


register = template.Library()


@register.tag(name="form")
def do_form(parser: template.base.Parser, token: template.base.Token) -> FormNode:
    """Block tag accepting an action name plus optional HTML attributes.

    The `validate`, `trigger`, `debounce`, `zone`, and `key` params compile to
    `data-next-*` attributes, while any other pair stays plain HTML.
    """
    bits = token.split_contents()
    if len(bits) < _MIN_FORM_TAG_BITS:
        msg = f"{bits[0]!r} tag requires the action name as its first argument"
        raise template.TemplateSyntaxError(msg)
    action_expr = parser.compile_filter(bits[1])
    attrs: list[tuple[str, FilterExpression]] = []
    partial_attrs: dict[str, FilterExpression] = {}
    for bit in bits[2:]:
        name, eq, value = bit.partition("=")
        if eq and name in _PARTIAL_FORM_PARAMS:
            partial_attrs[_PARTIAL_FORM_PARAMS[name]] = parser.compile_filter(value)
            continue
        attrs.append(_parse_form_attr(parser, bits[0], bit))
    nodelist = parser.parse(("endform",))
    parser.delete_first_token()
    return FormNode(
        action_expr=action_expr,
        nodelist=nodelist,
        attrs=tuple(attrs),
        partial_attrs=partial_attrs,
    )


@register.simple_tag(takes_context=True)
def action_url(context: template.Context, action_name: str) -> str:
    """Return the endpoint URL for an action, page-scoped like `{% form %}`."""
    name = str(action_name)
    if not name:
        msg = (
            "{% action_url %} resolved its argument to an empty action name. "
            "An unquoted name is looked up as a template variable, quote the "
            "action name to pass it as a literal."
        )
        raise FormActionNotFoundError(msg)
    anchor, _meta = anchor_lookup_from_context(context, name)
    return form_action_manager.get_action_url(name, page_path=anchor)


def _parse_form_attr(
    parser: template.base.Parser, tag_name: str, bit: str
) -> "tuple[str, FilterExpression]":
    """Parse one `key="value"` tag argument into an attribute name and value."""
    name, eq, value = bit.partition("=")
    if not eq or not name or not value:
        msg = (
            f"{tag_name!r} tag arguments after the action name must use "
            f'the key="value" form, got {bit!r}'
        )
        raise template.TemplateSyntaxError(msg)
    if name in _RESERVED_FORM_ATTRS or name.startswith(_RESERVED_FORM_ATTR_PREFIX):
        msg = f"{tag_name!r} tag reserves the {name!r} attribute for the framework"
        raise template.TemplateSyntaxError(msg)
    return name, parser.compile_filter(value)
