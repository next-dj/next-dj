"""The manager over every component source and the context keys it declares.

The live registry holds only what a request made the router walk, so the checks and the
JS-context key walk read a second manager built here, outside any `checks` module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, override

from django.template import Engine, Template, TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import FilterExpression, NodeList
from django.template.loader_tags import BlockNode, ExtendsNode, IncludeNode

from next.conf import next_framework_settings
from next.discovery import get_router_manager, iter_page_tree_component_folders
from next.utils import child_nodelists

from .context import component
from .manager import ComponentsManager
from .nodes import ComponentTagNode


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.base import Node

    from .info import ComponentInfo
    from .renderers import TemplateSource


type _Blocks = dict[str, tuple[NodeList, ...]]


class _ComponentsManagerCache:
    """The one manager a check run builds, instead of one per asking check."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.held: ComponentsManager | None = None


_components_manager_cache = _ComponentsManagerCache()


def get_components_manager() -> ComponentsManager:
    """Return a per-run cached `ComponentsManager` holding every component source.

    The manager is this module's own, because the live registry holds only what
    requests have already made the router walk. Dropped on `settings_reloaded`.
    """
    cached = _components_manager_cache.held
    if cached is not None:
        return cached
    manager = ComponentsManager()
    # This module owns the manager, so no listener hears about its backends.
    manager.reload(notify=False)
    _components_manager_cache.held = manager
    _register_page_tree_component_folders(manager)
    return manager


def _register_page_tree_component_folders(manager: ComponentsManager) -> None:
    """Register every components folder the configured page trees carry.

    The router's own folders and registration make a reader see what a render sees.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    for router in router_manager.backends:
        for folder, tree_root, route_trail in iter_page_tree_component_folders(router):
            manager.register_router_walk_folder(folder, tree_root, route_trail)


def reset_components_manager_cache(**kwargs) -> None:
    """Drop the cached `ComponentsManager` so the next read rebuilds it."""
    _components_manager_cache.held = None


def components_listed() -> bool:
    """Return whether `COMPONENT_BACKENDS` lists a backend for a check to walk."""
    configs = next_framework_settings.COMPONENT_BACKENDS
    return isinstance(configs, list) and bool(configs)


def import_component_modules() -> tuple[Path, ...]:
    """Run every `component.py` the listed backends hold and return their paths.

    Running one registers its context and its access, which the checks then read.
    """
    if not components_listed():
        return ()
    return tuple(
        module_path
        for backend in get_components_manager().backends
        for module_path in backend.import_component_modules()
    )


def iter_serialized_component_context_keys() -> Iterator[tuple[Path, str]]:
    """Yield the `component.py` path and key of every keyed `serialize=True` context.

    Reading the keys imports every `component.py`, even under `LAZY_COMPONENT_MODULES`.
    The keys of a keyless callable exist only at render time.
    """
    manager = get_components_manager()
    for backend in manager.backends:
        for module_path in backend.import_component_modules():
            for entry in component.get_functions(module_path):
                if entry.serialize and entry.key is not None:
                    yield module_path, entry.key


def _literal(expression: object) -> str | None:
    """Return the name an expression spells as a string, `None` for anything else."""
    if not isinstance(expression, FilterExpression):
        return None
    name = expression.var
    if expression.filters or not isinstance(name, str):
        return None
    return str(name)


@dataclass(frozen=True, slots=True)
class Position:
    """Where a walk stands, the block overrides it carries and the files on its path."""

    under: bool = False
    blocks: _Blocks = field(default_factory=dict)
    trail: frozenset[object] = frozenset()

    def entering(self, key: object) -> Position:
        """Return the position one file further down the path."""
        return replace(self, trail=self.trail | {key})


class TemplateWalk(ABC):
    """Walk every node a template reaches through component tags, include and extends.

    A walk answers `True` the moment a hook stops it, and each walk names its hooks.
    """

    def __init__(self, manager: ComponentsManager, anchor: Path) -> None:
        """Resolve components through `manager` from the template at `anchor`."""
        self.manager = manager
        self.anchor = anchor

    @abstractmethod
    def stops(self, node: Node) -> bool:
        """Return whether the walk ends at `node` before placing it."""

    @abstractmethod
    def place(self, node: Node, at: Position) -> None:
        """Note a node the walk reached at `at`."""

    @abstractmethod
    def inner(self, node: Node, at: Position) -> Position:
        """Return the position the child node lists of `node` are walked at."""

    @abstractmethod
    def source(self, name: str) -> TemplateSource | None:
        """Return the template of the component `name`, `None` when it is not walked."""

    @abstractmethod
    def lost(self) -> bool:
        """Return what a reference the walk cannot follow answers."""

    @abstractmethod
    def read(self, path: Path) -> None:
        """Note a file the walk read a template from."""

    def component(self, source: TemplateSource, at: Position) -> bool:
        """Walk the template of one component, once per path through the files."""
        if source.path in at.trail:
            return False
        self.read(source.path)
        try:
            compiled = Template(source.text)
        except TemplateSyntaxError:
            return self.lost()
        return self.template(
            compiled.nodelist, replace(at, blocks={}).entering(source.path)
        )

    def template(self, nodelist: NodeList, at: Position) -> bool:
        """Walk a template, through the parent it extends with its blocks merged."""
        extends = next(
            (node for node in nodelist if isinstance(node, ExtendsNode)), None
        )
        if extends is None:
            return self._nodes(nodelist, at)
        merged = dict(at.blocks)
        for name, block in extends.blocks.items():
            merged[name] = (*merged.get(name, ()), block.nodelist)
        return self._named(extends.parent_name, replace(at, blocks=merged))

    def _nodes(self, nodelist: NodeList, at: Position) -> bool:
        return any(self._node(node, at) for node in nodelist)

    def _node(self, node: Node, at: Position) -> bool:
        if self.stops(node):
            return True
        self.place(node, at)
        if isinstance(node, ComponentTagNode):
            return self._nodes(node.nodelist, at) or self._nested(node.name, at)
        if isinstance(node, IncludeNode):
            return self._named(node.template, replace(at, blocks={}))
        if isinstance(node, BlockNode):
            lists = (node.nodelist, *at.blocks.get(node.name, ()))
            return any(self._nodes(nodes, at) for nodes in lists)
        inner = self.inner(node, at)
        return any(self._nodes(child, inner) for child in child_nodelists(node))

    def _nested(self, name: str, at: Position) -> bool:
        source = self.source(name)
        if source is None:
            return self.lost()
        return self.component(source, at)

    def _named(self, expression: object, at: Position) -> bool:
        """Follow a template named by a string, losing the walk when it cannot."""
        name = _literal(expression)
        if name is None:
            return self.lost()
        if name in at.trail:
            return False
        try:
            template = Engine.get_default().get_template(name)
        except (TemplateDoesNotExist, TemplateSyntaxError):
            return self.lost()
        with suppress(OSError, ValueError):
            origin = Path(template.origin.name)
            if origin.is_file():
                self.read(origin)
        return self.template(template.nodelist, at.entering(name))


class _Reach(TemplateWalk):
    """Collect every component a walk reaches, each walked once, past any tag."""

    def __init__(self, manager: ComponentsManager, anchor: Path) -> None:
        """Start from no component found."""
        super().__init__(manager, anchor)
        self.found: dict[ComponentInfo, None] = {}

    @override
    def stops(self, node: Node) -> bool:
        return False

    @override
    def place(self, node: Node, at: Position) -> None:
        return

    @override
    def inner(self, node: Node, at: Position) -> Position:
        return at

    @override
    def source(self, name: str) -> TemplateSource | None:
        """Return the template of a component met for the first time, else `None`."""
        info = self.manager.get_component(name, self.anchor)
        if info is None or info in self.found:
            return None
        self.found[info] = None
        return self.manager.template_loader.load_source(info)

    @override
    def lost(self) -> bool:
        return False

    @override
    def read(self, path: Path) -> None:
        return


def reached_components(nodelist: NodeList, anchor: Path) -> tuple[ComponentInfo, ...]:
    """Return every component `nodelist` renders, through includes and nested ones.

    Each name resolves from `anchor` as a render of the page resolves it.
    """
    walk = _Reach(get_components_manager(), anchor)
    walk.template(nodelist, Position())
    return tuple(walk.found)


__all__ = [
    "Position",
    "TemplateWalk",
    "components_listed",
    "get_components_manager",
    "import_component_modules",
    "iter_serialized_component_context_keys",
    "reached_components",
    "reset_components_manager_cache",
]
