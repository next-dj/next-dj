"""Template tags for next-dj components (void/block ``{% component %}``, slots).

Props and slot HTML resolve from ``current_template_path`` and pass to the renderer.
"""

from __future__ import annotations

import difflib
import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, override

from django import template
from django.template.base import (
    FilterExpression,
    NodeList,
    Parser,
    Token,
    Variable,
    token_kwargs,
)
from django.utils.safestring import SafeString

from next.components import collect_visible_components, get_component, render_component
from next.components.nodes import ComponentTagNode, SetSlotTagNode, SlotTagNode
from next.components.renderers import COMPONENT_PROPS_CONTEXT_KEY, SLOT_KEY_PREFIX
from next.conf import fail_loudly, next_framework_settings
from next.seeding import COLLECTOR_KEY, REQUEST_KEY, TEMPLATE_PATH_KEY
from next.static import collect_component_assets
from next.utils import on_forget_resolved_trees


logger = logging.getLogger(__name__)

register = template.Library()

_SLOT_ARG_COUNT = 2

# Distinct ``current_template_path`` strings memoised, matching the other path caches.
_PATH_MEMO_MAX_ENTRIES = 2048

_END_BLOCK_COMPONENT = ("/component",)
_END_BLOCK_SLOT = ("/slot",)
_END_BLOCK_SET_SLOT = ("/set_slot",)

# The key a component pushes for its body to write slots into.
_SLOT_COLLECTOR_KEY = "_component_slots"

# Body-only keys. ``_component_props`` is not listed as every node overwrites it.
_INTERNAL_CONTEXT_KEYS = frozenset({_SLOT_COLLECTOR_KEY})

_DASH_BEFORE_DASH = re.compile(r"-(?=-)")

# Distinguishes an absent slot key from an empty one, only absence takes the default.
_SLOT_MISSING: Any = object()


def _strip_quotes(raw: str) -> str:
    return raw.strip("'\"").strip()


@functools.lru_cache(maxsize=_PATH_MEMO_MAX_ENTRIES)
def _resolve_template_path(raw: str) -> Path:
    """Return the resolved file for a raw ``current_template_path`` string.

    Memoised per raw string and dropped along with the shared page-tree resolutions.
    """
    return Path(raw).resolve()


on_forget_resolved_trees(_resolve_template_path.cache_clear)


def _comment_safe(text: str) -> str:
    """Break every `--` run so `text` cannot terminate an HTML comment early.

    An odd run would still close it, so a space follows each dash before a dash.
    """
    return _DASH_BEFORE_DASH.sub("- ", text)


def _parse_component(
    parser: Parser, token: Token, tag: str
) -> tuple[str, dict[str, FilterExpression]]:
    """Parse ``tag "name" key=expr …`` into the component name and its props.

    Django reads the props, so each value compiles to a render-time `FilterExpression`.
    """
    bits = token.split_contents()[1:]
    if not bits:
        msg = f"{tag} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits.pop(0))
    if not name:
        msg = f"{tag} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    return name, _parse_props(parser, bits)


def _parse_props(parser: Parser, bits: list[str]) -> dict[str, FilterExpression]:
    """Parse the `key=expr` props a component tag carries, consuming `bits`."""
    props: dict[str, FilterExpression] = {}
    while bits:
        props.update(token_kwargs(bits, parser))
        if bits:
            # Django stops at the first non-pair bit, an unmeant word, so it is dropped.
            del bits[0]
    return props


@dataclass(frozen=True, slots=True)
class _NamedTagSpec:
    """What one named slot tag needs to compile, in its paired or its short form."""

    empty_name_message: str
    wrong_arity_message: str
    end_tokens: tuple[str, ...] = ()


def _parse_named_tag(
    parser: Parser, token: Token, spec: _NamedTagSpec
) -> tuple[str, NodeList]:
    """Parse ``tag "name"`` into a name and the body only the paired form carries."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARG_COUNT:
        raise template.TemplateSyntaxError(spec.wrong_arity_message)
    name = _strip_quotes(bits[1])
    if not name:
        raise template.TemplateSyntaxError(spec.empty_name_message)
    if not spec.end_tokens:
        return name, NodeList()
    nodelist = parser.parse(spec.end_tokens)
    parser.delete_first_token()
    return name, nodelist


@dataclass(frozen=True, slots=True)
class _SlotCollector:
    """The slot dict a rendering component offers to the slots it owns."""

    owner: ComponentNode
    slots: dict[str, str]


class SlotNode(SlotTagNode):
    """Fills a named slot of the component whose body holds it."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Remember the slot name and nested nodes."""
        self.name = name
        self.nodelist = nodelist
        # Claimed by the compiling component, so a slot via include fills nothing.
        self.owner: ComponentNode | None = None

    def collect(self, context: template.Context, slots: dict[str, str]) -> None:
        """Append the rendered body to the entry this slot name holds.

        A repeat appends so a slot in ``{% for %}`` keeps every body in render order.
        """
        body = self.nodelist.render(context)
        previous = slots.get(self.name)
        slots[self.name] = body if previous is None else previous + body

    @override
    def render(self, context: template.Context) -> str:
        """Fill the owning component's slot, or render the body where it stands.

        A slot whose owner is not the component rendering around it renders in place.
        """
        collector = context.get(_SLOT_COLLECTOR_KEY)
        if isinstance(collector, _SlotCollector) and collector.owner is self.owner:
            self.collect(context, collector.slots)
            return ""
        return self.nodelist.render(context)


class ComponentNode(ComponentTagNode):
    """Looks up the component, gathers slots and free children, then renders HTML."""

    def __init__(
        self, name: str, props: dict[str, FilterExpression], nodelist: NodeList
    ) -> None:
        """Store component name, prop expressions, and nested nodes."""
        self.name = name
        self.props = props
        self.nodelist = nodelist
        # Prop names are fixed at compile time, so the guard rebuilds nothing.
        self.prop_names = frozenset(props)
        # Slots are fixed once compiled, so walk once, nested ones claim their own.
        claimed = [
            node
            for node in cast("list[SlotNode]", nodelist.get_nodes_by_type(SlotNode))
            if node.owner is None
        ]
        for node in claimed:
            node.owner = self
        self.has_slots = bool(claimed)

    def _resolved_props(self, context: template.Context) -> dict[str, Any]:
        """Resolve every prop expression against the active template context.

        ``FilterExpression`` marks a string literal safe, so raw HTML needs ``|safe``.
        """
        resolved: dict[str, Any] = {}
        for key, expr in self.props.items():
            value = expr.resolve(context)
            is_literal = not isinstance(expr.var, Variable)
            if is_literal and not expr.filters and isinstance(value, SafeString):
                # Django marks a bare literal safe, demote it so it autoescapes.
                value = str.__str__(value)
            resolved[key] = value
        return resolved

    def _template_path_from_context(self, context: template.Context) -> Path | None:
        """Return a resolved path from ``current_template_path``, or ``None``."""
        raw = context.get(TEMPLATE_PATH_KEY)
        # A ``Path`` was resolved by an earlier render, only ``str`` hits the disk.
        if isinstance(raw, Path):
            return raw
        if isinstance(raw, str):
            return _resolve_template_path(raw)
        return None

    def _close_match(self, path: Path) -> str | None:
        """Return the closest visible component name for a did-you-mean hint.

        The failed `get_component` lookup just built the visibility map, a cache hit.
        """
        names = collect_visible_components(path)
        matches = difflib.get_close_matches(self.name, sorted(names))
        return matches[0] if matches else None

    def _on_missing_path(self) -> str:
        """Report a render whose context carries no `current_template_path`.

        The context names no template, so there is no scope for a did-you-mean hint.
        """
        if not fail_loudly():
            logger.warning(
                "component '%s' skipped, no current_template_path in context", self.name
            )
            return ""
        if next_framework_settings.STRICT_LOADING:
            msg = (
                f"component '{self.name}' cannot be resolved because the "
                "template context has no current_template_path"
            )
            raise template.TemplateSyntaxError(msg)
        return (
            f"<!-- next: component '{_comment_safe(self.name)}' skipped, no "
            "current_template_path in context (no-discovery-path) -->"
        )

    def _on_not_found(self, path: Path) -> str:
        """Report a name no component visible from `path` answers."""
        if not fail_loudly():
            logger.warning("component '%s' not found from %s", self.name, path)
            return ""
        suggestion = self._close_match(path)
        if next_framework_settings.STRICT_LOADING:
            hint = f", did you mean '{suggestion}'?" if suggestion else ""
            msg = f"component '{self.name}' not found from {path}{hint}"
            raise template.TemplateSyntaxError(msg)
        hint = f", did you mean '{_comment_safe(suggestion)}'?" if suggestion else ""
        return (
            f"<!-- next: component '{_comment_safe(self.name)}' not found "
            f"(not-found){hint} -->"
        )

    def _render_children(
        self, context: template.Context, slots: dict[str, str]
    ) -> SafeString:
        """Render the body, routing every slot body into `slots`.

        A body holding no slot skips the collector push.
        """
        if not self.has_slots:
            return self.nodelist.render(context)
        with context.push(**{_SLOT_COLLECTOR_KEY: _SlotCollector(self, slots)}):
            return self.nodelist.render(context)

    @override
    def render(self, context: template.Context) -> str:
        """Merge props, slots, and children, then render the component."""
        path = self._template_path_from_context(context)
        if path is None:
            return self._on_missing_path()

        info = get_component(self.name, path)
        if info is None:
            return self._on_not_found(path)

        collect_component_assets(info, context.get(COLLECTOR_KEY))

        slots: dict[str, str] = {}
        children = self._render_children(context, slots)

        # ``flatten`` returns a fresh dict, so the render context is filled in place.
        render_ctx = cast("dict[str, Any]", context.flatten())
        for key in _INTERNAL_CONTEXT_KEYS:
            render_ctx.pop(key, None)
        render_ctx.update(self._resolved_props(context))
        render_ctx[TEMPLATE_PATH_KEY] = path
        # Children are finished markup inserted unescaped, the calling template escapes.
        render_ctx["children"] = children
        render_ctx[COMPONENT_PROPS_CONTEXT_KEY] = self.prop_names

        for slot_name, content in slots.items():
            # Slot content lives under ``slot_<name>`` so it cannot shadow a prop.
            render_ctx[f"{SLOT_KEY_PREFIX}{slot_name}"] = content

        request = render_ctx.get(REQUEST_KEY)
        if request is None:
            request = context.get(REQUEST_KEY)
        return render_component(info, render_ctx, request=request)


class SetSlotNode(SetSlotTagNode):
    """Uses injected slot HTML from the parent, or renders the fallback body."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Remember slot name and fallback nodes."""
        self.name = name
        self.nodelist = nodelist

    @override
    def render(self, context: template.Context) -> str:
        """Render injected slot HTML when present, otherwise the fallback body.

        Only a missing key falls back, not an explicitly empty slot.
        """
        slot_content = context.get(f"{SLOT_KEY_PREFIX}{self.name}", _SLOT_MISSING)
        if slot_content is _SLOT_MISSING:
            return self.nodelist.render(context)
        return str(slot_content)


@register.tag(name="component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile void ``{% component "name" … %}`` (no body, no closing tag)."""
    name, props = _parse_component(parser, token, "{% component %}")
    return ComponentNode(name=name, props=props, nodelist=NodeList())


@register.tag(name="#component")
def do_block_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile ``{% #component "name" … %}`` … ``{% /component %}``."""
    name, props = _parse_component(parser, token, "{% #component %}")
    nodelist = parser.parse(_END_BLOCK_COMPONENT)
    parser.delete_first_token()
    return ComponentNode(name=name, props=props, nodelist=nodelist)


_BLOCK_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% #slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #slot %} tag requires exactly one argument: slot name",
    end_tokens=_END_BLOCK_SLOT,
)

_SHORT_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% slot %} tag requires a quoted slot name",
    wrong_arity_message="{% slot %} short form requires exactly one quoted slot name",
)

_SET_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% #set_slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #set_slot %} tag requires exactly one argument: slot name",
    end_tokens=_END_BLOCK_SET_SLOT,
)

_SHORT_SET_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% set_slot %} tag requires a quoted slot name",
    wrong_arity_message=(
        "{% set_slot %} short form requires exactly one quoted slot name"
    ),
)


@register.tag(name="#slot")
def do_block_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile ``{% #slot "name" %}`` … ``{% /slot %}``."""
    name, nodelist = _parse_named_tag(parser, token, _BLOCK_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="slot")
def do_short_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile empty ``{% slot "name" %}`` (short slot, no body)."""
    name, nodelist = _parse_named_tag(parser, token, _SHORT_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="#set_slot")
def do_block_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile ``{% #set_slot "name" %}`` … ``{% /set_slot %}``."""
    name, nodelist = _parse_named_tag(parser, token, _SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)


@register.tag(name="set_slot")
def do_short_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile empty ``{% set_slot "name" %}`` (no default body, no closing tag)."""
    name, nodelist = _parse_named_tag(parser, token, _SHORT_SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)
