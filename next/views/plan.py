"""The optional request features one page can use, decided once and held per view."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Final, cast

from django.template import Node, Template, TemplateSyntaxError
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

from next.caches import SETTINGS
from next.components import components_manager, registry
from next.components.defer import defer_declarations
from next.components.tags import ComponentNode, SetSlotNode, SlotNode
from next.conf import next_framework_settings
from next.lifecycle import PageFacts, lifecycle
from next.marks import ContainerNode
from next.pages.declarations import page_declarations
from next.pages.loaders import layout_ids, module_counters
from next.pages.metadata.nodes import MetadataNode
from next.pages.nodes import LayoutSlotNode
from next.pages.paths import page_template_path
from next.pages.transactions import page_transaction
from next.partial.layers import LAYER_ATTR
from next.partial.zone import LOADING, ZoneNode
from next.permissions.nodes import HasPermNode
from next.requests import RequestKind
from next.router.links import active_links
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
from next.utils import template_edits_watched

from .containers import compile_containers
from .fixed import fixed_digests, slot_view


if TYPE_CHECKING:
    from collections.abc import Mapping, Set as AbstractSet
    from pathlib import Path

    from next.components.info import ComponentInfo
    from next.lifecycle import Points
    from next.pages.loaders import BoundaryChain
    from next.pages.manager import Page
    from next.router.links import TemplateLinks


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
        ContainerNode,
        LayoutSlotNode,
    }
)
_CALLING: Final = frozenset({ComponentNode, SlotNode, SetSlotNode, MetadataNode})
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

    `plain` holds when a safe request renders the template straight, nothing to gather,
    guard or drive. The points of a kind are asked of the participants on its first use.
    """

    links: bool
    marks: bool
    fragments: bool
    defers: bool
    streams: bool
    guarded: bool
    awaits: bool
    plain: bool
    layers: bool
    facts: PageFacts
    held: dict[tuple[RequestKind, bool], Points] = field(
        default_factory=dict, compare=False, repr=False
    )

    def points(self, kind: RequestKind, *, safe: bool) -> Points:
        """Return the points a request of `kind` takes, by the safety of its method."""
        key = (kind, safe)
        found = self.held.get(key)
        if found is None:
            facts = self.facts if safe else replace(self.facts, safe=False, plain=False)
            found = self.held[key] = lifecycle.points(replace(facts, kind=kind))
        return found


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


def inert_markup(template: Template) -> bool:
    """Whether `template` writes only what its source spells, every node inert.

    A component, an include or a tag of unknown output opens it to the whole project.
    """
    return not any(map(_open, template.nodelist.get_nodes_by_type(Node)))


def spells_container(template: Template, links: TemplateLinks) -> bool:
    """Whether an inert `template` can open a container `links` marks.

    A variable may hold markup marked safe and `now` writes its format unescaped, so
    either one can. Without them the source spells every tag, a zone wrapper included.
    """
    nodes = template.nodelist.get_nodes_by_type(Node)
    if any(isinstance(node, _UNSPELLED) for node in nodes):
        return True
    source = template.source
    literal = _BLOCKS.sub("", source)
    return links.holds(f"{source}{literal}{_zone_openers(nodes)}")


@dataclass(frozen=True, slots=True)
class _Reach:
    """What the component calls one template can make decide for its page.

    `templates` holds each component template walked, which a development edit swaps.
    """

    defers: bool
    guarded: bool
    layers: bool
    templates: tuple[Template, ...]
    infos: tuple[ComponentInfo, ...]


def _seen(node: Node) -> bool:
    """Whether every component call `node` can render is a node of the walk."""
    kind = type(node)
    if kind is ZoneNode:
        return cast("ZoneNode", node).options.reload != LOADING
    if kind is SimpleNode:
        return cast("SimpleNode", node).func in _INERT_TAGS
    return kind in _INERT or kind in _CALLING


def spells_layer(template: Template) -> bool:
    """Whether the source of `template` spells the attribute of a layer link."""
    return LAYER_ATTR in template.source.lower()


def _held(info: ComponentInfo, deferring: AbstractSet[Path]) -> tuple[bool, bool]:
    """Return whether calls of `info` may defer and whether it holds an error UI."""
    files = info.files
    defers = info.module_path in deferring or (
        files is not None and files.loading is not None
    )
    return defers, files is not None and files.error is not None


def reach(template: Template, page: Path) -> _Reach | None:
    """Return what the components `template` calls can do, through their own calls.

    A tag of unknown output, a `render()` function or lazily loaded modules may call
    any component, so the walk answers `None` for them.
    """
    if next_framework_settings.LAZY_COMPONENT_MODULES:
        return None
    renderer = components_manager.component_renderer
    loader = components_manager.template_loader
    deferring = defer_declarations.deferring_modules()
    found: dict[int, ComponentInfo] = {}
    walked: list[Template] = []
    pending = [template]
    defers = guarded = layers = False
    while pending:
        current = pending.pop()
        layers = layers or spells_layer(current)
        for node in current.nodelist.get_nodes_by_type(Node):
            if not _seen(node):
                return None
            if not isinstance(node, ComponentNode):
                continue
            options = node.options
            defers = defers or (options is not None and options.mode is not None)
            info = components_manager.get_component(node.name, page)
            if info is None or id(info) in found:
                continue
            found[id(info)] = info
            if not renderer.templated(info):
                return None
            may, failing = _held(info, deferring)
            defers, guarded = defers or may, guarded or failing
            try:
                compiled = loader.load_template(info)
            except TemplateSyntaxError:
                # The render raises it where the call stands, the plan stays blind.
                return None
            if compiled is not None:
                walked.append(compiled)
                pending.append(compiled)
    return _Reach(defers, guarded, layers, tuple(walked), tuple(found.values()))


def _fresh(held: _Reach | None) -> bool:
    """Whether the component templates a reach walked are still those the loader gives.

    Only a process that watches template edits reads them again.
    """
    if held is None or not held.templates or not template_edits_watched():
        return True
    loader = components_manager.template_loader
    try:
        current = [loader.load_template(info) for info in held.infos]
    except TemplateSyntaxError:
        return False
    walked = [compiled for compiled in current if compiled is not None]
    return len(walked) == len(held.templates) and all(
        a is b for a, b in zip(walked, held.templates, strict=True)
    )


def _components() -> tuple[int, ...]:
    """Return the counters a component reach depends on."""
    versions = (backend.version for backend in components_manager.backends)
    return (defer_declarations.version, *versions)


def _fixed(page: Page, file_path: Path) -> Mapping[str, tuple[str, str | None]]:
    """Return the digests the layouts of fixed chrome took from their source."""
    skeleton = page.layout_skeleton(file_path)[0]
    return fixed_digests(skeleton, layout_ids(file_path))


class PagePlans:
    """Hold the plan of one page while everything it was decided from stays the same.

    One tuple holds the stamp of every counter and template, the plan and its reach.
    """

    __slots__ = ("_blind", "_file_path", "_held", "_page")

    def __init__(self, page: Page, file_path: Path) -> None:
        """Start before the first request of the page."""
        self._page = page
        self._file_path = file_path
        self._held: tuple[tuple[object, ...], PagePlan, _Reach | None] | None = None
        self._blind: tuple[tuple[object, ...], PagePlan] | None = None

    def _stamped(self, shape: object) -> tuple[object, ...]:
        """Return what a plan of the page composed as `shape` was decided from."""
        return (
            shape,
            SETTINGS.version,
            _VERSION.value,
            _GENERATION.value,
            page_declarations.version,
            self._page.epoch,
            registry.deferred_components.held,
            registry.error_files.held,
            _components(),
            lifecycle.version,
        )

    def of(self, shape: Template | BoundaryChain) -> PagePlan:
        """Return the plan of the page composed as `shape`, built again once it moved.

        `shape` is the compiled template, or the boundary chain of a `render()` body.
        """
        stamp = self._stamped(shape)
        held = self._held
        if held is not None and stamp == held[0] and _fresh(held[2]):
            return held[1]
        plan, reached = self._build(shape)
        self._held = (stamp, plan, reached)
        return plan

    def blind(self) -> PagePlan:
        """Return the plan of the page read without its template, open to anything.

        A reload, an async `render()` and a module broken at build are planned so.
        """
        stamp = self._stamped(None)
        held = self._blind
        if held is not None and stamp == held[0]:
            return held[1]
        plan = self._build(None)[0]
        self._blind = (stamp, plan)
        return plan

    def _build(
        self, shape: Template | BoundaryChain | None
    ) -> tuple[PagePlan, _Reach | None]:
        """Decide the plan and the reach it read, a `render()` body open to anything."""
        page, file_path = self._page, self._file_path
        links = active_links()
        fragments = False
        reached = None
        layers = True
        template = None
        chain: BoundaryChain | None
        if isinstance(shape, Template):
            template = shape
            chain = page.composed_chain(file_path)
            opened = not inert_markup(shape)
            marks = links is not None and (opened or spells_container(shape, links))
            fragments = (
                marks
                and links is not None
                and links.compiled
                and compile_containers(shape, links)
            )
            if opened:
                reached = reach(shape, page_template_path(file_path))
                layers = reached is None or reached.layers
            else:
                layers = spells_layer(shape)
        else:
            chain = shape
            opened = True
            marks = links is not None
        deferring = reached is None or reached.defers
        failing = reached is None or reached.guarded
        deferred = chain is None or bool(chain.deferred)
        streams = deferred or registry.deferred_components.held
        defers = deferred or (opened and streams and deferring)
        guarded = chain is None or bool(chain.errors)
        guarded = guarded or (opened and registry.error_files.held and failing)
        awaits = shape is None or page.awaits(file_path)
        plain = not (defers or guarded or awaits)
        facts = PageFacts(
            file_path=file_path,
            kind=RequestKind.PAGE,
            safe=True,
            template=template,
            chain=chain,
            defers=defers,
            streams=streams,
            guarded=guarded,
            plain=plain,
            atomic=page_transaction(file_path),
            links=links is not None,
            marks=marks,
            fragments=fragments,
            layers=layers,
            fixed=partial(_fixed, page, file_path),
            slot=None if template is None else partial(slot_view, template),
            compiler=compile_containers,
        )
        plan = PagePlan(
            links=links is not None,
            marks=marks,
            fragments=fragments,
            defers=defers,
            streams=streams,
            guarded=guarded,
            awaits=awaits,
            plain=plain,
            layers=layers,
            facts=facts,
        )
        return plan, reached


__all__ = [
    "PagePlan",
    "PagePlans",
    "inert_markup",
    "reach",
    "spells_container",
    "spells_layer",
]
