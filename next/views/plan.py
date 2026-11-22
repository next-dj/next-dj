"""The optional request features one page can use, decided once and held per view."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.db import connections
from django.template import Node, Template
from django.template.base import TextNode, VariableNode
from django.template.defaultfilters import safe, safeseq
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

from next.components import registry
from next.conf import next_framework_settings
from next.conf.router import router_enabled
from next.pages.loaders import module_counters
from next.pages.transactions import atomic_store, page_atomic
from next.partial.zone import LOADING, ZoneNode
from next.permissions.nodes import HasPermNode
from next.router.current import current_links
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
from next.transactions import safe_aliases


if TYPE_CHECKING:
    from pathlib import Path

    from next.pages.loaders import BoundaryChain
    from next.pages.manager import Page
    from next.router.current import CurrentLinks


_INERT: Final = frozenset(
    {
        TextNode,
        VariableNode,
        CommentNode,
        CsrfTokenNode,
        ForNode,
        HasPermNode,
        IfChangedNode,
        IfNode,
        LoadNode,
        NowNode,
        SpacelessNode,
        TemplateTagNode,
        VerbatimNode,
        WithNode,
        RootLayoutNode,
        TemplatePlaceholderNode,
        ZoneNode,
    }
)
_INERT_TAGS: Final = frozenset(
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
_VERSION, _GENERATION = module_counters
_UNESCAPED: Final = frozenset({safe, safeseq})
_BLOCKS: Final = re.compile(r"\{%.*?%\}|\{#.*?#\}", re.DOTALL)
_UNSPELLED: Final = (VariableNode, NowNode)


@dataclass(frozen=True, slots=True)
class PagePlan:
    """What one page uses of the features a request may need, decided ahead of it.

    `plain` holds when a safe request needs no block beyond the link pass and `streams`.
    """

    router: bool
    links: CurrentLinks | None
    marks: bool
    reads: tuple[str, ...] | None
    defers: bool
    streams: bool
    guarded: bool
    awaits: bool
    plain: bool


def _open(node: Node) -> bool:
    """Whether `node` can write markup its template source does not spell.

    A variable through `safe` can, and so can a zone that stashes a loading skeleton.
    """
    if isinstance(node, VariableNode):
        return any(func in _UNESCAPED for func, _ in node.filter_expression.filters)
    if isinstance(node, ZoneNode):
        return node.options.reload == LOADING
    if type(node) is SimpleNode:
        return node.func not in _INERT_TAGS
    return type(node) not in _INERT


def _zone_openers(nodes: list[Node]) -> str:
    """Return the opening tag of each zone wrapper, which the source never spells."""
    return "".join(
        f"<{node.options.tag}{node.options.attrs}>"
        for node in nodes
        if isinstance(node, ZoneNode)
    )


def sealed(template: Template) -> bool:
    """Whether `template` writes only what its source spells, every node inert.

    A component, an include or a tag of unknown output opens it to the whole project.
    """
    return not any(map(_open, template.nodelist.get_nodes_by_type(Node)))


def spells_container(template: Template, links: CurrentLinks) -> bool:
    """Whether a sealed `template` can open a container `links` marks.

    A variable may hold markup marked safe and `now` writes its format unescaped, so
    either one can. Without them the source spells every tag, a zone wrapper included.
    """
    nodes = template.nodelist.get_nodes_by_type(Node)
    if any(isinstance(node, _UNSPELLED) for node in nodes):
        return True
    source = template.source
    literal = _BLOCKS.sub("", source)
    return links.holds(f"{source}{literal}{_zone_openers(nodes)}")


class PagePlans:
    """Hold the plan of one page while everything it was decided from stays the same.

    The stamp is each settings object, registry counter and template behind the plan.
    """

    __slots__ = ("_file_path", "_held", "_page", "_stamp")

    def __init__(self, page: Page, file_path: Path) -> None:
        """Start before the first request of the page."""
        self._page = page
        self._file_path = file_path
        self._stamp: tuple[object, ...] | None = None
        self._held: PagePlan | None = None

    def of(self, shape: Template | BoundaryChain) -> PagePlan:
        """Return the plan of the page composed as `shape`, built again once it moved.

        `shape` is the compiled template, or the boundary chain of a `render()` body.
        """
        stamp = (
            shape,
            next_framework_settings.ROUTER,
            next_framework_settings.TRANSACTIONS,
            connections.settings,
            _VERSION.value,
            _GENERATION.value,
            atomic_store.version,
            self._page.epoch,
            registry.deferred_components.held,
            registry.error_files.held,
        )
        held = self._held
        if held is not None and stamp == self._stamp:
            return held
        held = self._held = self._build(shape)
        self._stamp = stamp
        return held

    def _build(self, shape: Template | BoundaryChain) -> PagePlan:
        """Decide the plan, a `render()` body open to whatever the project holds."""
        page, file_path = self._page, self._file_path
        router = router_enabled()
        links = current_links()
        if isinstance(shape, Template):
            chain = page.composed_chain(file_path)
            opened = not sealed(shape)
            marks = links is not None and (opened or spells_container(shape, links))
        else:
            chain = shape
            opened = True
            marks = links is not None
        reads = safe_aliases(page_atomic(file_path))
        streams = bool(chain.deferred) or registry.deferred_components.held
        defers = bool(chain.deferred) or (opened and streams)
        guarded = bool(chain.errors) or (opened and registry.error_files.held)
        awaits = page.awaits(file_path)
        return PagePlan(
            router=router,
            links=links,
            marks=marks,
            reads=reads,
            defers=defers,
            streams=streams,
            guarded=guarded,
            awaits=awaits,
            plain=not (router or defers or guarded or awaits or reads != ()),
        )


__all__ = ["PagePlan", "PagePlans", "sealed", "spells_container"]
