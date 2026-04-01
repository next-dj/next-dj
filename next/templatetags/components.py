"""Template tags for next-dj components (``{% component %}``, slots).

Resolve from ``current_template_path``, collect nested ``{% slot %}`` blocks, and
pass props and slot HTML to the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from django import template
from django.template.base import Node, NodeList, Parser, Token

from next.components import get_component, render_component


register = template.Library()

_COMPONENT_NAME_INDEX = 1
_SLOT_ARG_COUNT = 2
_COMPONENT_MIN_BITS = 2

_END_COMPONENT = ("endcomponent", "/component")
_END_SLOT = ("endslot", "/slot")
_END_SET_SLOT = ("endset_slot", "/set_slot")

# Slot collection uses this key during parent `{% component %}` body render
_INTERNAL_CONTEXT_KEYS = frozenset({"_component_slots"})


def _strip_quotes(raw: str) -> str:
    return raw.strip("'\"").strip()


@dataclass(frozen=True, slots=True)
class _NamedBlockSpec:
    """Arguments shared by ``{% slot %}`` and ``{% set_slot %}`` compilation."""

    end_tokens: tuple[str, ...]
    expected_bits: int
    empty_name_message: str
    wrong_arity_message: str


def _parse_one_named_block(
    parser: Parser,
    token: Token,
    spec: _NamedBlockSpec,
) -> tuple[str, NodeList]:
    """Parse ``tag "name"`` … ``end`` into a name and inner node list."""
    bits = token.split_contents()
    if len(bits) != spec.expected_bits:
        raise template.TemplateSyntaxError(spec.wrong_arity_message)
    name = _strip_quotes(bits[_COMPONENT_NAME_INDEX])
    if not name:
        raise template.TemplateSyntaxError(spec.empty_name_message)
    nodelist = parser.parse(spec.end_tokens)
    parser.delete_first_token()
    return name, nodelist


class SlotNode(Node):
    """Renders the slot body and records it by name when under ``{% component %}``."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Remember the slot name and nested nodes."""
        self.name = name
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Render the body and write into ``_component_slots`` when that dict exists."""
        body = self.nodelist.render(context)
        slots = context.get("_component_slots")
        if isinstance(slots, dict):
            slots[self.name] = body
        return body


class ComponentNode(Node):
    """Looks up the component, gathers slots and free children, then renders HTML."""

    def __init__(
        self,
        name: str,
        props: dict[str, str],
        nodelist: NodeList,
    ) -> None:
        """Store component name, static props from the tag, and nested nodes."""
        self.name = name
        self.props = props
        self.nodelist = nodelist

    def _template_path_from_context(self, context: template.Context) -> Path | None:
        """Return a resolved path from ``current_template_path``, or ``None``."""
        raw = context.get("current_template_path")
        if raw is None:
            return None
        if isinstance(raw, Path):
            path = raw
        elif isinstance(raw, str):
            path = Path(raw)
        else:
            return None
        return path.resolve()

    def render(self, context: template.Context) -> str:
        """Merge props, slots, and children, then render the component."""
        path = self._template_path_from_context(context)
        if path is None:
            return ""

        info = get_component(self.name, path)
        if info is None:
            return ""

        slots: dict[str, str] = {}
        child_chunks: list[str] = []
        with context.push(_component_slots=slots):
            for node in self.nodelist:
                if isinstance(node, SlotNode):
                    node.render(context)
                else:
                    child_chunks.append(node.render(context))

        parent_flat = dict(cast("dict[str, Any]", context.flatten()))
        for key in _INTERNAL_CONTEXT_KEYS:
            parent_flat.pop(key, None)
        render_ctx: dict[str, Any] = {**parent_flat, **self.props}
        render_ctx["children"] = "".join(child_chunks)

        for slot_name, content in slots.items():
            render_ctx[f"slot_{slot_name}"] = content
            render_ctx[slot_name] = content

        request = render_ctx.get("request")
        if request is None:
            request = context.get("request")
        return render_component(info, render_ctx, request=request)


class SetSlotNode(Node):
    """Uses injected slot HTML from the parent, or renders the fallback body."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Remember slot name and fallback nodes."""
        self.name = name
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Prefer ``slot_<name>`` / ``<name>`` from context over the inner template."""
        slot_content = context.get(f"slot_{self.name}") or context.get(self.name)
        if slot_content is not None and slot_content != "":
            return str(slot_content)
        return self.nodelist.render(context)


@register.tag(name="component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile ``{% component "name" … %}`` … ``{% endcomponent %}``."""
    bits = token.split_contents()
    if len(bits) < _COMPONENT_MIN_BITS:
        msg = "{% component %} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits[_COMPONENT_NAME_INDEX])
    if not name:
        msg = "{% component %} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    props: dict[str, str] = {}
    for part in bits[2:]:
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        props[key.strip()] = _strip_quotes(val)
    nodelist = parser.parse(_END_COMPONENT)
    parser.delete_first_token()
    return ComponentNode(name=name, props=props, nodelist=nodelist)


_SLOT_SPEC = _NamedBlockSpec(
    end_tokens=_END_SLOT,
    expected_bits=_SLOT_ARG_COUNT,
    empty_name_message="{% slot %} tag requires a quoted slot name",
    wrong_arity_message="{% slot %} tag requires exactly one argument: slot name",
)

_SET_SLOT_SPEC = _NamedBlockSpec(
    end_tokens=_END_SET_SLOT,
    expected_bits=_SLOT_ARG_COUNT,
    empty_name_message="{% set_slot %} tag requires a quoted slot name",
    wrong_arity_message=("{% set_slot %} tag requires exactly one argument: slot name"),
)


@register.tag(name="slot")
def do_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile ``{% slot "name" %}`` … ``{% endslot %}``."""
    name, nodelist = _parse_one_named_block(parser, token, _SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="set_slot")
def do_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile ``{% set_slot "name" %}`` … ``{% endset_slot %}``."""
    name, nodelist = _parse_one_named_block(parser, token, _SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)
