"""Template tags for next-dj components: {% component %}, {% slot %}, {% set_slot %}."""

from __future__ import annotations

from pathlib import Path

from django import template
from django.template.base import Node, NodeList, Parser, Token

from next.components import (
    get_component,
    render_component,
)


register = template.Library()

_SLOT_ARGS_COUNT = 2
_COMPONENT_MIN_ARGS = 2


class SlotNode(Node):
    """Node for {% slot "name" %} ... {% endslot %} content."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Store slot name and nodelist."""
        self.name = name
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Render slot content and optionally store in context for component."""
        rendered = self.nodelist.render(context)
        slots = context.get("_component_slots")
        if slots is not None and isinstance(slots, dict):
            slots[self.name] = rendered
        return rendered


class ComponentNode(Node):
    """Node for {% component "name" %} ... {% endcomponent %} with optional slots."""

    def __init__(
        self,
        name: str,
        props: dict[str, str],
        nodelist: NodeList,
    ) -> None:
        """Store component name, props and nodelist."""
        self.name = name
        self.props = props
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Render component after collecting slots and children."""
        template_path = context.get("current_template_path")
        if template_path is None:
            return ""
        path = Path(template_path) if isinstance(template_path, (str, Path)) else None
        if path is None:
            return ""
        path = path.resolve()
        info = get_component(self.name, path)
        if info is None:
            return ""
        slots: dict[str, str] = {}
        children_parts: list[str] = []
        with context.push(_component_slots=slots):
            for node in self.nodelist:
                if isinstance(node, SlotNode):
                    node.render(context)
                else:
                    children_parts.append(node.render(context))
        children = "".join(children_parts)
        request = context.get("request")
        component_context = dict(self.props)
        component_context["children"] = children
        for slot_name, slot_content in slots.items():
            component_context[f"slot_{slot_name}"] = slot_content
            component_context[slot_name] = slot_content
        return render_component(info, component_context, request=request)


@register.tag(name="component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    """Block tag {% component "name" key="val" %} ... {% endcomponent %}."""
    bits = token.split_contents()
    if len(bits) < _COMPONENT_MIN_ARGS:
        msg = "{% component %} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = bits[1].strip("'\"")
    if not name:
        msg = "{% component %} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    props: dict[str, str] = {}
    for i in range(2, len(bits)):
        part = bits[i]
        if "=" in part:
            key, _, val = part.partition("=")
            props[key.strip()] = val.strip("'\"").strip()
    nodelist = parser.parse(("endcomponent", "/component"))
    parser.delete_first_token()
    return ComponentNode(name=name, props=props, nodelist=nodelist)


@register.tag(name="slot")
def do_slot(parser: Parser, token: Token) -> SlotNode:
    """Block tag {% slot "name" %} ... {% endslot %}."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARGS_COUNT:
        msg = "{% slot %} tag requires exactly one argument: slot name"
        raise template.TemplateSyntaxError(msg)
    name = bits[1].strip("'\"")
    if not name:
        msg = "{% slot %} tag requires a quoted slot name"
        raise template.TemplateSyntaxError(msg)
    nodelist = parser.parse(("endslot", "/slot"))
    parser.delete_first_token()
    return SlotNode(name=name, nodelist=nodelist)


class SetSlotNode(Node):
    """Node for {% set_slot "name" %} fallback {% endset_slot %} in component."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Store slot name and fallback nodelist."""
        self.name = name
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Output slot content from context or fallback."""
        slot_content = context.get(f"slot_{self.name}") or context.get(self.name)
        if slot_content is not None and slot_content != "":
            return str(slot_content)
        return self.nodelist.render(context)


@register.tag(name="set_slot")
def do_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Block tag {% set_slot "name" %} fallback {% endset_slot %} in component."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARGS_COUNT:
        msg = "{% set_slot %} tag requires exactly one argument: slot name"
        raise template.TemplateSyntaxError(msg)
    name = bits[1].strip("'\"")
    if not name:
        msg = "{% set_slot %} tag requires a quoted slot name"
        raise template.TemplateSyntaxError(msg)
    nodelist = parser.parse(("endset_slot", "/set_slot"))
    parser.delete_first_token()
    return SetSlotNode(name=name, nodelist=nodelist)
