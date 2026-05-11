"""Template tags for next-dj components (void/block ``{% component %}``, slots).

Resolve from ``current_template_path``, collect nested ``{% #slot %}`` /
``{% slot %}`` blocks, and pass props and slot HTML to the renderer.

In component templates, use ``{% #set_slot %}`` … ``{% /set_slot %}`` or the
short void ``{% set_slot "name" %}`` when there is no default slot body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from django import template
from django.template import base as template_base
from django.template.base import (
    FilterExpression,
    Node,
    NodeList,
    Parser,
    Token,
    Variable,
)
from django.utils.safestring import SafeString

from next.components import get_component, render_component
from next.static import default_manager


# Allow line breaks inside ``{% ... %}`` (multiline tag bodies).
template_base.tag_re = re.compile(template_base.tag_re.pattern, re.DOTALL)

register = template.Library()

_COMPONENT_NAME_INDEX = 1
_SLOT_ARG_COUNT = 2
_COMPONENT_MIN_BITS = 2

_END_BLOCK_COMPONENT = ("/component",)
_END_BLOCK_SLOT = ("/slot",)
_END_BLOCK_SET_SLOT = ("/set_slot",)

_SHORT_SLOT_EMPTY_NAME = "{% slot %} tag requires a quoted slot name"
_SHORT_SET_SLOT_EMPTY_NAME = "{% set_slot %} tag requires a quoted slot name"

# Slot collection uses this key during parent ``{% #component %}`` body render
_INTERNAL_CONTEXT_KEYS = frozenset({"_component_slots"})

# Sentinel distinguishing "slot key absent" from "slot present but empty".
# An explicitly empty slot (``{% #slot "x" %}{% /slot %}``) renders nothing,
# whereas a missing slot falls back to the ``{% #set_slot %}`` default body.
_SLOT_MISSING: Any = object()


def _strip_quotes(raw: str) -> str:
    return raw.strip("'\"").strip()


def _parse_props(
    parser: Parser,
    bits: list[str],
    start: int,
) -> dict[str, FilterExpression]:
    """Parse ``key=expr`` pairs from tag bits starting at *start*.

    Each ``expr`` is compiled into a Django :class:`FilterExpression`
    so the value resolves against the template context at render time.
    Quoted strings, numbers, dotted attribute lookups, and filter
    chains all work through the same mechanism. Bits without ``=``
    are skipped to keep the tag tolerant of stray tokens.
    """
    props: dict[str, FilterExpression] = {}
    for part in bits[start:]:
        if "=" not in part:
            continue
        key, _, raw = part.partition("=")
        props[key.strip()] = FilterExpression(raw, parser)
    return props


@dataclass(frozen=True, slots=True)
class _NamedBlockSpec:
    """Arguments shared by ``{% #slot %}`` and ``{% #set_slot %}`` compilation."""

    end_tokens: tuple[str, ...]
    expected_bits: int
    empty_name_message: str
    wrong_arity_message: str


def _parse_one_named_block(
    parser: Parser,
    token: Token,
    spec: _NamedBlockSpec,
) -> tuple[str, NodeList]:
    """Parse ``tag "name"`` … ``/end`` into a name and inner node list."""
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
    """Renders the slot body and records it by name when under ``{% #component %}``."""

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
        props: dict[str, FilterExpression],
        nodelist: NodeList,
    ) -> None:
        """Store component name, prop expressions, and nested nodes."""
        self.name = name
        self.props = props
        self.nodelist = nodelist

    def _resolved_props(self, context: template.Context) -> dict[str, Any]:
        """Resolve every prop expression against the active template context.

        Django's ``FilterExpression`` marks bare string literals as safe so
        ``{% tag "x" %}`` style arguments would render unescaped if interpolated
        through ``{{ x }}``. Component props are user-facing text by default,
        so we strip the safe marker from plain string literals. Callers who
        want raw HTML can opt in explicitly with ``prop=value|safe`` or with
        a variable that already holds a ``SafeString``.
        """
        resolved: dict[str, Any] = {}
        for key, expr in self.props.items():
            value = expr.resolve(context)
            is_literal = not isinstance(expr.var, Variable)
            if is_literal and not expr.filters and isinstance(value, SafeString):
                # Bare string literal that Django marked safe by convention.
                # Demote to a plain ``str`` so ``{{ prop }}`` autoescapes it.
                value = str.__str__(value)
            resolved[key] = value
        return resolved

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

        collector = context.get("_static_collector")
        if collector is not None and not info.is_simple:
            default_manager.discover_component_assets(info, collector)

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
        render_ctx: dict[str, Any] = {
            **parent_flat,
            **self._resolved_props(context),
        }
        render_ctx["current_template_path"] = path
        render_ctx["children"] = "".join(child_chunks)

        for slot_name, content in slots.items():
            # Slot content lives exclusively under ``slot_<name>`` so prop
            # names never collide with slot names. The unprefixed key would
            # shadow a same-named prop and make ``{% #set_slot %}`` defaults
            # unreachable when a prop happened to share the slot's name.
            render_ctx[f"slot_{slot_name}"] = content

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
        """Render injected slot HTML when present, otherwise the fallback body.

        Slot content is looked up under the prefixed ``slot_<name>`` key only.
        Props live in the unprefixed namespace and never shadow slot defaults,
        even when a prop happens to share a slot's name. An explicitly empty
        slot (``{% #slot "x" %}{% /slot %}``) renders as the empty string. Only
        a missing slot key falls back to the inner template.
        """
        slot_content = context.get(f"slot_{self.name}", _SLOT_MISSING)
        if slot_content is _SLOT_MISSING:
            return self.nodelist.render(context)
        return str(slot_content)


@register.tag(name="component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile void ``{% component "name" … %}`` (no body, no closing tag)."""
    bits = token.split_contents()
    if len(bits) < _COMPONENT_MIN_BITS:
        msg = "{% component %} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits[_COMPONENT_NAME_INDEX])
    if not name:
        msg = "{% component %} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    props = _parse_props(parser, bits, 2)
    return ComponentNode(name=name, props=props, nodelist=NodeList())


@register.tag(name="#component")
def do_block_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile ``{% #component "name" … %}`` … ``{% /component %}``."""
    bits = token.split_contents()
    if len(bits) < _COMPONENT_MIN_BITS:
        msg = "{% #component %} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits[_COMPONENT_NAME_INDEX])
    if not name:
        msg = "{% #component %} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    props = _parse_props(parser, bits, 2)
    nodelist = parser.parse(_END_BLOCK_COMPONENT)
    parser.delete_first_token()
    return ComponentNode(name=name, props=props, nodelist=nodelist)


_BLOCK_SLOT_SPEC = _NamedBlockSpec(
    end_tokens=_END_BLOCK_SLOT,
    expected_bits=_SLOT_ARG_COUNT,
    empty_name_message="{% #slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #slot %} tag requires exactly one argument: slot name",
)

_SET_SLOT_SPEC = _NamedBlockSpec(
    end_tokens=_END_BLOCK_SET_SLOT,
    expected_bits=_SLOT_ARG_COUNT,
    empty_name_message="{% #set_slot %} tag requires a quoted slot name",
    wrong_arity_message=(
        "{% #set_slot %} tag requires exactly one argument: slot name"
    ),
)


@register.tag(name="#slot")
def do_block_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile ``{% #slot "name" %}`` … ``{% /slot %}``."""
    name, nodelist = _parse_one_named_block(parser, token, _BLOCK_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="slot")
def do_short_slot(_parser: Parser, token: Token) -> SlotNode:
    """Compile empty ``{% slot "name" %}`` (short slot, no body)."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARG_COUNT:
        msg = "{% slot %} short form requires exactly one quoted slot name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits[_COMPONENT_NAME_INDEX])
    if not name:
        raise template.TemplateSyntaxError(_SHORT_SLOT_EMPTY_NAME)
    return SlotNode(name=name, nodelist=NodeList())


@register.tag(name="#set_slot")
def do_block_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile ``{% #set_slot "name" %}`` … ``{% /set_slot %}``."""
    name, nodelist = _parse_one_named_block(parser, token, _SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)


@register.tag(name="set_slot")
def do_short_set_slot(_parser: Parser, token: Token) -> SetSlotNode:
    """Compile empty ``{% set_slot "name" %}`` (no default body, no closing tag)."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARG_COUNT:
        msg = "{% set_slot %} short form requires exactly one quoted slot name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits[_COMPONENT_NAME_INDEX])
    if not name:
        raise template.TemplateSyntaxError(_SHORT_SET_SLOT_EMPTY_NAME)
    return SetSlotNode(name=name, nodelist=NodeList())
