"""Compile the navigation containers of a template into nodes that mark their own links.

The page then pays for the size of its navigation, not for the size of its document.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast

from django.template import Node, NodeList, Template
from django.template.base import TextNode, VariableNode
from django.template.defaulttags import (
    CommentNode,
    CsrfTokenNode,
    ForNode,
    IfChangedNode,
    IfNode,
    LoadNode,
    NowNode,
    SpacelessNode,
    TemplateTagNode,
    VerbatimNode,
    WithNode,
)
from django.template.library import SimpleNode

from next.components.tags import ComponentNode
from next.marks import ContainerNode
from next.pages.metadata.nodes import MetadataNode
from next.pages.nodes import LayoutSlotNode
from next.partial.zone import ZONE_ATTR, ZoneNode
from next.permissions.nodes import HasPermNode
from next.router.links import fold
from next.templatetags.next_static import (
    asset,
    collect_head,
    collect_scripts,
    collect_styles,
    use_module,
    use_script,
    use_style,
)
from next.templatetags.pages import RootLayoutNode, TemplatePlaceholderNode
from next.utils import RAW_TEXT_ELEMENTS, VOID_ELEMENTS


if TYPE_CHECKING:
    from next.router.links import TemplateLinks


type _Event = tuple[str, str, int]

_NAME: Final = re.compile(r"</?([a-zA-Z][^\t\n\f\r />]*)")
_RAW: Final = frozenset({*RAW_TEXT_ELEMENTS, "template", "plaintext"})
_TEXT, _TAG, _QUOTED, _RAW_TEXT, _COMMENT = range(5)
_HELD: Final = "_next_containers"
_NEUTRAL: Final = (
    VariableNode,
    MetadataNode,
    CommentNode,
    CsrfTokenNode,
    LoadNode,
    NowNode,
    TemplateTagNode,
)
_NEUTRAL_TAGS: Final = frozenset(
    {
        asset,
        collect_head,
        collect_scripts,
        collect_styles,
        use_module,
        use_script,
        use_style,
    }
)
_BLOCKS: Final = (
    ForNode,
    WithNode,
    SpacelessNode,
    IfChangedNode,
    HasPermNode,
    LayoutSlotNode,
    RootLayoutNode,
    TemplatePlaceholderNode,
    ZoneNode,
)
_lock = threading.Lock()


class _BlindError(Exception):
    """A node sequence whose containers cannot be found from the source alone."""


@dataclass(slots=True)
class _Lexer:
    """Where the HTML tokenizer stands at one point of a template source."""

    mode: int = _TEXT
    quote: str = ""
    raw: str = ""
    tag: str = ""
    closing: bool = False

    def state(self) -> tuple[int, str, str, bool]:
        """Return what of the state the next character is read under, to compare."""
        mode = self.mode
        if mode in {_TAG, _QUOTED}:
            quote = self.quote if mode == _QUOTED else ""
            return (mode, quote, self.tag, self.closing)
        return (mode, self.raw if mode == _RAW_TEXT else "", "", False)

    def copy(self) -> _Lexer:
        """Return a lexer standing where this one does."""
        return _Lexer(self.mode, self.quote, self.raw, self.tag, self.closing)

    def feed(self, text: str) -> list[_Event]:
        """Read `text` and return its start tags, end tags and tag ends in order."""
        events: list[_Event] = []
        at, size = 0, len(text)
        while at < size:
            at = self._step(text, at, events)
        return events

    def _step(self, text: str, at: int, events: list[_Event]) -> int:
        """Consume the text from `at` in the current mode and return where it stops."""
        mode = self.mode
        if mode == _TEXT:
            return self._in_text(text, at, events)
        if mode == _TAG:
            return self._in_tag(text, at, events)
        if mode == _QUOTED:
            end, step = text.find(self.quote, at), 1
        elif mode == _RAW_TEXT:
            end, step = fold(text).find(f"</{self.raw}", at), 0
        else:
            end, step = text.find("-->", at), 3
        if end < 0:
            return len(text)
        self.mode = _TAG if mode == _QUOTED else _TEXT
        return end + step

    def _in_text(self, text: str, at: int, events: list[_Event]) -> int:
        """Move to the next markup in text mode, reading a tag name it opens."""
        lt = text.find("<", at)
        if lt < 0:
            return len(text)
        if text.startswith("<!--", lt):
            self.mode = _COMMENT
            return lt + 4
        found = _NAME.match(text, lt)
        if found is None:
            if text.startswith(("<!", "<?"), lt):
                self.mode, self.tag, self.closing = _TAG, "", True
                return lt + 2
            return lt + 1
        self.closing = text[lt + 1] == "/"
        self.tag = found[1].lower()
        self.mode = _TAG
        events.append(("close" if self.closing else "open", self.tag, lt))
        return found.end()

    def _in_tag(self, text: str, at: int, events: list[_Event]) -> int:
        """Move past a quoted value or the end of the tag being read."""
        for index in range(at, len(text)):
            char = text[index]
            if char in "\"'":
                self.mode, self.quote = _QUOTED, char
                return index + 1
            if char == ">":
                events.append(("shut" if self.closing else "end", self.tag, index))
                self.mode = _TEXT
                if not self.closing and self.tag in _RAW:
                    self.mode, self.raw = _RAW_TEXT, self.tag
                return index + 1
        return len(text)


@dataclass(slots=True)
class _Opening:
    """A start tag being read, the piece it began in and the literal text seen of it."""

    name: str
    node: int
    offset: int
    text: list[str] = field(default_factory=list)
    dynamic: bool = False


@dataclass(slots=True)
class _Pending:
    """An open container, where its nodes begin and how deep its own name nests."""

    name: str
    start: int
    depth: int = 1


class _Compiler:
    """Wrap each container of one template, raising `_BlindError` where it is blind.

    `apply` off reads the whole template first, so a blind spot changes nothing.
    """

    def __init__(self, links: TemplateLinks, *, apply: bool) -> None:
        """Hold the backend the containers are told by."""
        self.links = links
        self.apply = apply
        self.names = links.names()
        self.attrs = links.attributes()

    def container(self, opening: _Opening) -> bool:
        """Whether the start tag read so far may open a container a selector names.

        A tag with a value it cannot read may hold any attribute, so it counts as one.
        """
        if opening.name in self.names:
            return True
        if opening.dynamic:
            return bool(self.attrs)
        return self.links.opens("".join(opening.text))

    def nodes(
        self, nodelist: NodeList, lexer: _Lexer, *, wrapping: bool
    ) -> dict[str, int]:
        """Walk `nodelist`, wrapping its containers, and return how it moves each name.

        Inside an open container nothing is wrapped again, as the outer one marks all.
        """
        walk = _Walk(self, lexer, wrapping=wrapping)
        for node in list(nodelist):
            walk.node(node)
        if walk.pending is not None or walk.opening is not None:
            raise _BlindError
        if self.apply:
            nodelist[:] = walk.out
        return walk.balance


class _Walk:
    """One pass over the nodes of a node list, the rewritten list built as it goes."""

    def __init__(self, compiler: _Compiler, lexer: _Lexer, *, wrapping: bool) -> None:
        """Start before the first node."""
        self.compiler = compiler
        self.lexer = lexer
        self.wrapping = wrapping
        self.out: list[Node] = []
        self.pending: _Pending | None = None
        self.opening: _Opening | None = None
        self.balance: dict[str, int] = {}

    def node(self, node: Node) -> None:
        """Take one node into the walk, raising `_BlindError` for an unknown output."""
        if isinstance(node, TextNode):
            self.text(node)
        elif isinstance(node, _NEUTRAL) or (
            type(node) is SimpleNode and node.func in _NEUTRAL_TAGS
        ):
            if self.opening is not None:
                self.opening.dynamic = True
            self.out.append(node)
        elif isinstance(node, ComponentNode):
            if self.lexer.mode != _TEXT or self.opening is not None:
                raise _BlindError
            self.out.append(node)
        elif isinstance(node, IfNode):
            self.branches(node, [nodes for _, nodes in node.conditions_nodelists])
        elif isinstance(node, _BLOCKS):
            self.block(node)
        elif isinstance(node, VerbatimNode):
            if self.compiler.links.holds(node.content):
                raise _BlindError
            self.lexer.feed(node.content)
            self.out.append(node)
        else:
            raise _BlindError

    def block(self, node: Node) -> None:
        """Take a block tag, a zone whose own wrapper is a container left blind."""
        if isinstance(node, ZoneNode) and self.compiler.links.holds(
            f'<{node.options.tag} {ZONE_ATTR}="{node.name}"{node.options.attrs}>'
        ):
            raise _BlindError
        lists = [getattr(node, name, None) for name in node.child_nodelists]
        self.branches(node, [nodes for nodes in lists if isinstance(nodes, NodeList)])

    def branches(self, node: Node, lists: list[NodeList]) -> None:
        """Walk each node list of a block from one state, all ending where it began.

        A branch that moves the open container is blind. One inside a start tag only
        makes that tag dynamic, its attributes unknown.
        """
        entry = self.lexer.state()
        opening = self.opening
        wrapping = self.wrapping and self.pending is None and opening is None
        for nodes in lists:
            branch = self.lexer.copy()
            moved = self.compiler.nodes(nodes, branch, wrapping=wrapping)
            if branch.state() != entry:
                raise _BlindError
            pending = self.pending
            if pending is not None and moved.get(pending.name, 0):
                raise _BlindError
        if opening is not None:
            opening.dynamic = True
        self.out.append(node)

    def text(self, node: TextNode) -> None:
        """Take one text node, splitting it where a container opens or closes."""
        text = node.s
        cut = 0
        for kind, name, at in self.lexer.feed(text):
            if kind == "open":
                self.balance[name] = self.balance.get(name, 0) + 1
                self.started(name, at - cut)
            elif kind == "close":
                self.balance[name] = self.balance.get(name, 0) - 1
                pending = self.pending
                if pending is not None and name == pending.name:
                    pending.depth -= 1
            else:
                cut = self.ended(name, (text, cut, at), node, closing=kind == "shut")
        opening = self.opening
        if opening is not None:
            here = opening.node == len(self.out)
            opening.text.append(text[cut + opening.offset if here else cut :])
        self.piece(node, text[cut:])

    def started(self, name: str, offset: int) -> None:
        """Note a start tag `offset` into the text the node has not cut yet."""
        pending = self.pending
        if pending is not None:
            if name == pending.name:
                pending.depth += 1
            return
        if self.wrapping:
            self.opening = _Opening(name, len(self.out), offset)

    def ended(
        self, name: str, span: tuple[str, int, int], node: TextNode, *, closing: bool
    ) -> int:
        """Note the end of a tag, opening or closing a container there, and cut."""
        text, cut, at = span
        opening = self.opening
        if opening is not None:
            self.opening = None
            here = opening.node == len(self.out)
            opening.text.append(text[cut + opening.offset if here else cut : at + 1])
            if not self.compiler.container(opening):
                return cut
            self.piece(node, text[cut : at + 1])
            self.begin(opening)
            if name in VOID_ELEMENTS:
                self.close()
            return at + 1
        pending = self.pending
        if (
            pending is not None
            and pending.depth == 0
            and closing
            and name == pending.name
        ):
            self.piece(node, text[cut : at + 1])
            self.close()
            return at + 1
        return cut

    def piece(self, node: TextNode, text: str) -> None:
        """Append a text node holding `text`, carrying the origin of `node`."""
        if text:
            self.out.append(_text(text, node))

    def begin(self, opening: _Opening) -> None:
        """Open a container at the start tag `opening` names, split out of its piece."""
        index = opening.node
        first = cast("TextNode", self.out[index])
        head, tail = first.s[: opening.offset], first.s[opening.offset :]
        parts = [_text(part, first) for part in (head, tail) if part]
        self.out[index : index + 1] = parts
        self.pending = _Pending(opening.name, index + (1 if head else 0))

    def close(self) -> None:
        """Wrap the nodes of the open container into one marking node."""
        pending = cast("_Pending", self.pending)
        self.pending = None
        nodes = NodeList(self.out[pending.start :])
        fixed = None
        if self.compiler.apply and all(type(node) is TextNode for node in nodes):
            text = "".join(cast("TextNode", node).s for node in nodes)
            fixed = self.compiler.links.fixed(text)
        wrapper = ContainerNode(nodes, fixed)
        wrapper.origin, wrapper.token = nodes[0].origin, nodes[0].token
        self.out[pending.start :] = [wrapper]


def _text(text: str, source: TextNode) -> TextNode:
    """Return a text node holding `text`, with the origin and token of `source`."""
    piece = TextNode(text)
    piece.origin, piece.token = source.origin, source.token
    return piece


def compile_containers(template: Template, links: TemplateLinks) -> bool:
    """Wrap the containers of `template` once, whether its source shows every one.

    A container behind a tag of unknown output keeps the document pass, and one a
    variable writes as safe markup is left to the client.
    """
    held = template.__dict__.get(_HELD)
    if held is None:
        with _lock:
            held = template.__dict__.get(_HELD) or (
                links.containers,
                _compiled(template, links),
            )
            setattr(template, _HELD, held)
    return bool(held[0] == links.containers and held[1])


def _compiled(template: Template, links: TemplateLinks) -> bool:
    """Wrap the containers of `template` when a dry walk finds no blind spot."""
    try:
        _Compiler(links, apply=False).nodes(template.nodelist, _Lexer(), wrapping=True)
    except _BlindError:
        return False
    _Compiler(links, apply=True).nodes(template.nodelist, _Lexer(), wrapping=True)
    return True


__all__ = ["compile_containers"]
