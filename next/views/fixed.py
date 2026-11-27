"""The chrome digests a layout of fixed markup takes once, from its source alone.

Such chrome renders the same bytes for every page and visitor, so its id answers it.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from django.template import Template, TemplateSyntaxError
from django.template.base import TextNode
from django.template.defaulttags import CommentNode, CsrfTokenNode, LoadNode
from django.template.library import SimpleNode

from next.caches import SETTINGS, BoundedCache
from next.marks import ContainerNode
from next.pages.metadata.nodes import MetadataNode
from next.pages.nodes import LayoutSlotNode
from next.partial.zone import ZoneNode
from next.router.digest import DIGEST_BYTES
from next.templatetags.next_static import collect_head, collect_scripts, collect_styles


if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.template import Context, Node, NodeList


type Fixed = Mapping[str, tuple[str, str | None]]

_BODY: Final = re.compile(r"<body(?![\w:-])", re.IGNORECASE)
_FIXED: Final = (TextNode, ZoneNode, MetadataNode, CommentNode, LoadNode, CsrfTokenNode)
_SLOTS: Final = frozenset({collect_head, collect_scripts, collect_styles})
_QUIET: Final = (TextNode, CommentNode, LoadNode)
_EFFECTS: Final = (MetadataNode, CsrfTokenNode)
_VIEWS: Final = "_next_slot_views"
# Parsed with the default engine, whose builtins a `TEMPLATES` change may move.
_held: BoundedCache[str, Fixed] = BoundedCache(64, depends=(SETTINGS,))
_lock = threading.Lock()


def _digest(ident: str, part: str) -> str:
    """Return the digest standing for one part of the fixed chrome of `ident`."""
    raw = f"{ident}\x00{part}".encode()
    return hashlib.blake2b(raw, digest_size=DIGEST_BYTES).hexdigest()


def _fixed(node: Node) -> bool:
    """Whether `node` writes the same chrome for every render once normalised.

    A zone or the head tags are a region the digest leaves out, and a token is emptied.
    """
    if isinstance(node, _FIXED):
        return True
    return type(node) is SimpleNode and node.func in _SLOTS


def _slot(nodes: NodeList, ident: str) -> LayoutSlotNode | None:
    """Return the slot of `ident` among `nodes` when every other node is fixed."""
    found: LayoutSlotNode | None = None
    for node in nodes:
        if isinstance(node, LayoutSlotNode) and node.ident == ident:
            found = node
        elif not _fixed(node):
            return None
    return found


def _read(skeleton: str, ids: Sequence[str]) -> Fixed:
    """Return the digests of the outer layouts of `skeleton` whose chrome is fixed.

    The chain stops at the first layout whose chrome depends on its render.
    """
    try:
        nodes = Template(skeleton).nodelist
    except TemplateSyntaxError:
        return {}
    found: dict[str, tuple[str, str | None]] = {}
    for index, ident in enumerate(ids):
        slot = _slot(nodes, ident)
        if slot is None:
            break
        if index == 0 and any(
            isinstance(node, TextNode) and _BODY.search(node.s) for node in nodes
        ):
            head = _digest(ident, "head")
            found[ident] = (head + _digest(ident, "body"), head)
        else:
            found[ident] = (_digest(ident, "chrome"), None)
        nodes = slot.nodelist
    return found


def fixed_digests(skeleton: str, ids: Sequence[str]) -> Fixed:
    """Return the digests of the layouts of `skeleton` that need no render, held."""
    held = _held.get(skeleton)
    if held is None:
        held = _held[skeleton] = _read(skeleton, ids)
    return held


class SlotView:
    """The slot of one layout rendered alone, the kept chrome run for its effects.

    The chrome writes nothing a navigation sends, so only its metadata and token run.
    """

    __slots__ = ("_after", "_before", "_slot", "template")

    def __init__(
        self,
        template: Template,
        slot: LayoutSlotNode,
        effects: tuple[tuple[Node, ...], tuple[Node, ...]],
    ) -> None:
        """Hold the page, its slot and the chrome nodes run before and after it."""
        self.template = template
        self._slot = slot
        self._before, self._after = effects

    def render(self, context: Context) -> str:
        """Render the slot in `context`, bound to the page as its template binds it."""
        template = self.template
        with context.render_context.push_state(template):
            if context.template is not None:
                return self._render(context)
            with context.bind_template(template):
                context.template_name = template.name
                return self._render(context)

    def _render(self, context: Context) -> str:
        for node in self._before:
            node.render_annotated(context)
        html = self._slot.nodelist.render(context)
        for node in self._after:
            node.render_annotated(context)
        return html


def _quiet(node: Node) -> bool:
    """Whether `node` writes chrome alone, with no effect a navigation needs."""
    if isinstance(node, _QUIET):
        return True
    if type(node) is SimpleNode:
        return node.func in _SLOTS
    if isinstance(node, ContainerNode):
        return all(map(_quiet, node.nodelist))
    return False


class _Walk:
    """One walk of the chrome down to a slot, the effects on either side of it noted."""

    __slots__ = ("after", "before", "ident", "target")

    def __init__(self, ident: str) -> None:
        """Start above the chrome, before the slot of `ident`."""
        self.ident = ident
        self.before: list[Node] = []
        self.after: list[Node] = []
        self.target: LayoutSlotNode | None = None

    def nodes(self, nodes: NodeList) -> bool:
        """Walk `nodes`, false once one may write what the navigation needs."""
        for node in nodes:
            if self.target is None and isinstance(node, LayoutSlotNode):
                if node.ident == self.ident:
                    self.target = node
                elif not self.nodes(node.nodelist):
                    return False
            elif isinstance(node, _EFFECTS):
                (self.before if self.target is None else self.after).append(node)
            elif not _quiet(node):
                return False
        return True


def slot_view(template: Template, ident: str) -> SlotView | None:
    """Return how a navigation renders the slot of `ident` alone, held per template.

    A slot inside a container, a zone or any wrapper of the chrome gets none.
    """
    held: dict[str, SlotView | None] | None = template.__dict__.get(_VIEWS)
    if held is not None and ident in held:
        return held[ident]
    walk = _Walk(ident)
    view = None
    if walk.nodes(template.nodelist) and walk.target is not None:
        effects = (tuple(walk.before), tuple(walk.after))
        view = SlotView(template, walk.target, effects)
    with _lock:
        template.__dict__.setdefault(_VIEWS, {})[ident] = view
    return view


__all__ = ["Fixed", "SlotView", "fixed_digests", "slot_view"]
