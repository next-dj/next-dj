"""Template tags for next-dj components (void/block ``{% component %}``, slots).

The nodes they compile to and the render of a call live in `next.components.tags`.
"""

from django import template
from django.template.base import NodeList, Parser, Token

from next.components.parsing import (
    BLOCK_SLOT_SPEC,
    END_BLOCK_COMPONENT,
    SET_SLOT_SPEC,
    SHORT_SET_SLOT_SPEC,
    SHORT_SLOT_SPEC,
    parse_component,
    parse_named_tag,
)
from next.components.tags import ComponentNode, SetSlotNode, SlotNode


register = template.Library()


@register.tag(name="component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile void ``{% component "name" … %}`` (no body, no closing tag)."""
    call = parse_component(parser, token, "{% component %}")
    return ComponentNode(
        call.name,
        call.props,
        NodeList(),
        options=call.options,
        index=call.index,
        captured=call.captured,
        perms=call.perms,
    )


@register.tag(name="#component")
def do_block_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile ``{% #component "name" … %}`` … ``{% /component %}``."""
    call = parse_component(parser, token, "{% #component %}")
    nodelist = parser.parse(END_BLOCK_COMPONENT)
    parser.delete_first_token()
    return ComponentNode(
        call.name,
        call.props,
        nodelist,
        options=call.options,
        index=call.index,
        captured=call.captured,
        perms=call.perms,
    )


@register.tag(name="#slot")
def do_block_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile ``{% #slot "name" %}`` … ``{% /slot %}``."""
    name, nodelist = parse_named_tag(parser, token, BLOCK_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="slot")
def do_short_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile empty ``{% slot "name" %}`` (short slot, no body)."""
    name, nodelist = parse_named_tag(parser, token, SHORT_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="#set_slot")
def do_block_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile ``{% #set_slot "name" %}`` … ``{% /set_slot %}``."""
    name, nodelist = parse_named_tag(parser, token, SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)


@register.tag(name="set_slot")
def do_short_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile empty ``{% set_slot "name" %}`` (no default body, no closing tag)."""
    name, nodelist = parse_named_tag(parser, token, SHORT_SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)


__all__ = ["register"]
