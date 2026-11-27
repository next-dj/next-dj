"""Where a component call sits in its template, read from the nodes around it."""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Self, cast
from weakref import WeakKeyDictionary

from django.template.base import (
    FilterExpression,
    Node,
    NodeList,
    Template,
    TextNode,
    VariableNode,
)
from django.template.defaulttags import ForNode
from django.template.loader_tags import BlockNode, IncludeNode
from django.templatetags.cache import CacheNode

from next.boundaries.markup import wrapper_tag
from next.boundaries.options import DEFAULT_TAG
from next.utils import child_nodelists, open_elements
from next.wire import KEY_ATTR

from .nodes import ComponentTagNode


if TYPE_CHECKING:
    from collections.abc import Callable

    from django import template


_KEYED: Final = re.compile(
    rf"""(?:^|\s){KEY_ATTR}\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)')"""
)
_HOLE: Final = re.compile("\x00([0-9]+)\x00")

_captures: WeakKeyDictionary[Template, tuple[IncludeNode, ...]] = WeakKeyDictionary()
_contents: WeakKeyDictionary[
    Template, tuple[frozenset[int], tuple[IncludeNode, ...]]
] = WeakKeyDictionary()

type _Piece = str | FilterExpression


def captured_includes(template: Template) -> tuple[IncludeNode, ...]:
    """Return the `{% include %}` nodes `template` holds inside a `{% cache %}` body."""
    held = _captures.get(template)
    if held is None:
        caches = cast("list[CacheNode]", template.nodelist.get_nodes_by_type(CacheNode))
        held = _captures[template] = tuple(
            include
            for cache in caches
            for include in cast(
                "list[IncludeNode]", cache.nodelist.get_nodes_by_type(IncludeNode)
            )
        )
    return held


def _content(template: Template) -> tuple[frozenset[int], tuple[IncludeNode, ...]]:
    """Return the component calls and the includes `template` holds, at any depth."""
    held = _contents.get(template)
    if held is None:
        nodelist = template.nodelist
        held = _contents[template] = (
            frozenset(map(id, nodelist.get_nodes_by_type(ComponentTagNode))),
            tuple(cast("list[IncludeNode]", nodelist.get_nodes_by_type(IncludeNode))),
        )
    return held


def included(
    node: Node, includes: tuple[IncludeNode, ...], seen: dict[Any, Any]
) -> bool:
    """Whether the templates `includes` rendered in this render hold `node`.

    Django keeps the template each include rendered in the bottom render context.
    """
    pending = list(includes)
    visited: set[int] = set()
    while pending:
        for held in seen.get(pending.pop(), {}).values():
            if id(held) in visited:
                continue
            visited.add(id(held))
            calls, nested = _content(held)
            if id(node) in calls:
                return True
            pending.extend(nested)
    return False


def _text_before(
    nodelist: NodeList, target: Callable[[Node], bool], parts: list[str]
) -> bool:
    """Gather the template text before the node `target` picks, True once found."""
    for node in nodelist:
        if target(node):
            return True
        if isinstance(node, TextNode):
            parts.append(node.s)
            continue
        if any(_text_before(child, target, parts) for child in child_nodelists(node)):
            return True
    return False


@dataclass(slots=True)
class _Walk:
    """The template pieces before a call, from the innermost loop body around it."""

    pieces: list[_Piece] = field(default_factory=list)
    looped: bool = False


def _pieces_before(
    nodelist: NodeList, target: Callable[[Node], bool], walk: _Walk
) -> bool:
    """Gather the text and variables before the node `target` picks, True once found."""
    for node in nodelist:
        if target(node):
            return True
        if isinstance(node, TextNode):
            walk.pieces.append(node.s)
        elif isinstance(node, VariableNode):
            walk.pieces.append(node.filter_expression)
        elif isinstance(node, ForNode):
            inner = _Walk()
            loop = node.nodelist_loop
            if isinstance(loop, NodeList) and _pieces_before(loop, target, inner):
                walk.pieces = inner.pieces
                walk.looped = True
                return True
        elif any(
            _pieces_before(child, target, walk) for child in child_nodelists(node)
        ):
            return True
    return False


def _keyed_row(pieces: list[_Piece]) -> tuple[_Piece, ...] | None:
    """Return the `data-next-key` value of the innermost open element, as pieces."""
    holes: list[FilterExpression] = []
    text: list[str] = []
    for piece in pieces:
        if isinstance(piece, str):
            text.append(piece)
        else:
            text.append(f"\x00{len(holes)}\x00")
            holes.append(piece)
    for _name, attrs in reversed(open_elements("".join(text))):
        keyed = _KEYED.search(attrs)
        if keyed is not None:
            value = keyed["double"] if keyed["double"] is not None else keyed["single"]
            return tuple(
                holes[int(part)] if position % 2 else part
                for position, part in enumerate(_HOLE.split(value))
                if position % 2 or part
            )
    return None


def finds(nodelist: NodeList, target: Callable[[Node], bool]) -> bool:
    """Whether `nodelist` holds the node `target` picks, at any depth."""
    return any(
        target(node) or any(finds(child, target) for child in child_nodelists(node))
        for node in nodelist
    )


def rendering_blocks(context: template.Context) -> tuple[BlockNode, ...]:
    """Return the blocks rendering around the current node, the outermost first."""
    return tuple(
        block
        for layer in context.dicts
        if isinstance(block := layer.get("block"), BlockNode)
    )


def nodelists(blocks: tuple[BlockNode, ...]) -> tuple[NodeList, ...]:
    """Return the nodelists of `blocks`, which outlive the copies Django renders."""
    return tuple(block.nodelist for block in blocks)


def same(held: tuple[NodeList, ...], found: tuple[NodeList, ...]) -> bool:
    """Whether `held` and `found` are the same nodelists, compared by identity."""
    return len(held) == len(found) and all(map(operator.is_, held, found))


@dataclass(frozen=True, slots=True)
class Placement:
    """What the static markup before one call decides, worked out once per site."""

    tag: str | None
    recipe: tuple[_Piece, ...] | None

    @classmethod
    def of(cls, path: tuple[tuple[NodeList, Callable[[Node], bool]], ...]) -> Self:
        """Read the markup before each step of `path`, a nodelist and the node it holds.

        The text decides the wrapper, the pieces of the innermost loop body the row key.
        """
        parts: list[str] = []
        walk = _Walk()
        for nodelist, target in path:
            _text_before(nodelist, target, parts)
            _pieces_before(nodelist, target, walk)
        recipe = _keyed_row(walk.pieces) if walk.looped else None
        return cls(wrapper_tag("".join(parts), DEFAULT_TAG), recipe)


type _Placed = tuple[tuple[NodeList, ...] | None, Placement]


@dataclass(slots=True)
class Known:
    """What one call works out once and keeps, outside the node a template shares.

    `placed` keeps a placement per template rendering the call, with the blocks around.
    """

    site: str = ""
    key: str = ""
    placed: WeakKeyDictionary[Template, _Placed] = field(
        default_factory=WeakKeyDictionary
    )


def significant(node: Node) -> bool:
    """Whether `node` is more than whitespace between the tags of a component."""
    return not isinstance(node, TextNode) or bool(node.s.strip())


def block_named(name: str) -> Callable[[Node], bool]:
    """Return the test that picks the `{% block %}` named `name`."""

    def placing(node: Node) -> bool:
        return isinstance(node, BlockNode) and node.name == name

    return placing


__all__ = [
    "Known",
    "Placement",
    "block_named",
    "captured_includes",
    "finds",
    "included",
    "nodelists",
    "rendering_blocks",
    "same",
    "significant",
]
