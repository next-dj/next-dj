"""The zones a standalone render of a page refuses, and the blocks it asks again.

The `next.E163` check, the zone registry and the zone render read this one analysis.
"""

from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

from django.template import Context
from django.template.base import (
    FilterExpression,
    Node,
    NodeList,
    TextNode,
    Variable,
    VariableNode,
)
from django.template.defaultfilters import default, default_if_none, safe
from django.template.defaulttags import (
    AutoEscapeControlNode,
    ForNode,
    IfNode,
    SpacelessNode,
    WithNode,
)
from django.template.library import TagHelperNode
from django.template.loader_tags import BlockNode, ExtendsNode, IncludeNode

from next.caches import BoundedCache
from next.components.nodes import ComponentTagNode, SetSlotTagNode, SlotTagNode
from next.components.renderers import SLOT_KEY_PREFIX
from next.components.sources import Position, TemplateWalk
from next.pages.paths import page_template_path
from next.permissions.nodes import HasPermNode
from next.seeding import REQUEST_KEY
from next.utils import child_nodelists, template_edits_watched

from .zone import ZoneNode, zones_under


if TYPE_CHECKING:
    from django.http import HttpRequest

    from next.components.manager import ComponentsManager
    from next.components.renderers import TemplateSource


type Stamps = frozenset[tuple[Path, float]]
"""The files an answer read, each with the modification time it was read at."""

type _Read = set[tuple[Path, float]]
type _Placed = list[tuple[Node, bool]]

_CONDITIONS: Final = (IfNode, ForNode)
_BODY: Final = "children"
_KNOWN_MODULES: Final = ("django.", "next.")
_PASSING_FILTERS: Final = (safe, default, default_if_none)
_TRANSPARENT: Final = (
    AutoEscapeControlNode,
    BlockNode,
    ComponentTagNode,
    SlotTagNode,
    SpacelessNode,
    WithNode,
    ZoneNode,
)
_STRUCTURAL: Final = (*_TRANSPARENT, ExtendsNode, ForNode, IfNode, IncludeNode)
_NAMED: Final = (BlockNode, ComponentTagNode, SetSlotTagNode, SlotTagNode)


@dataclass(frozen=True, slots=True)
class _Verdict:
    guarding: bool
    stamps: Stamps


_verdicts: BoundedCache[tuple[Path, Path, str, str], _Verdict] = BoundedCache()


def fresh(stamps: Stamps) -> bool:
    """Return whether every file an answer read is unchanged since it was read."""
    return all(_mtime(path) == mtime for path, mtime in stamps)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _root(value: object) -> str | None:
    """Return the first lookup of a variable or of the variable an expression reads."""
    if isinstance(value, FilterExpression):
        value = value.var
    if isinstance(value, Variable) and value.lookups:
        return value.lookups[0]
    return None


def _arguments(expression: FilterExpression) -> Iterator[str]:
    """Yield the first lookup of every variable a filter of the expression takes."""
    for _func, args in expression.filters:
        for lookup, arg in args:
            name = _root(arg) if lookup else None
            if name is not None:
                yield name


def _names(value: object) -> Iterator[str]:
    """Yield every variable name an expression or a bare variable reads."""
    root = _root(value)
    if root is not None:
        yield root
    if isinstance(value, FilterExpression):
        yield from _arguments(value)


def _parts(node: Node) -> Iterator[object]:
    """Yield every value a node holds outside its child nodes, containers unpacked."""
    pending: list[object] = list(vars(node).values())
    while pending:
        value = pending.pop()
        if isinstance(value, Node | NodeList):
            continue
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list | tuple):
            pending.extend(value)
        else:
            yield value


type _Source = tuple[object, ...] | NodeList


def _bindings(node: Node) -> Iterator[tuple[frozenset[str], _Source]]:
    """Yield each set of names a node binds with what it binds them to.

    A component tag binds its `children` and a slot tag the slot to the nodes they hold.
    """
    if isinstance(node, ForNode):
        loopvars = node.loopvars
        names = [loopvars] if isinstance(loopvars, str) else loopvars
        yield frozenset(names), (node.sequence,)
    elif isinstance(node, ComponentTagNode):
        yield frozenset({_BODY}), node.nodelist
    elif isinstance(node, SlotTagNode):
        yield frozenset({f"{SLOT_KEY_PREFIX}{node.name}"}), node.nodelist
    for value in vars(node).values():
        if isinstance(value, dict):
            for key, expression in value.items():
                if isinstance(key, str):
                    yield frozenset({key}), (expression,)


class _Body:
    """The variable names that may hold one part of the body a page hands over.

    The part is `children` or one `slot_<name>`, every binding of it grows the names.
    """

    def __init__(self, placed: _Placed, part: str) -> None:
        """Start from the variable of `part` and grow it through every binding."""
        self._part = part
        self.names = {part}
        bindings = [binding for node, _under in placed for binding in _bindings(node)]
        grown = True
        while grown:
            grown = False
            for names, source in bindings:
                if not names <= self.names and self._reads(source):
                    self.names |= names
                    grown = True

    def _reads(self, source: _Source) -> bool:
        if isinstance(source, NodeList):
            nodes = source.get_nodes_by_type(Node)
            return any(self._touched(node) for node in nodes)
        return self._read(source)

    def _touched(self, node: Node) -> bool:
        """Return whether a node held by a tag may carry the part into what it binds."""
        if isinstance(node, VariableNode):
            return self._read((node.filter_expression,))
        if isinstance(node, SetSlotTagNode):
            return f"{SLOT_KEY_PREFIX}{node.name}" in self.names
        if isinstance(node, ComponentTagNode | IncludeNode):
            return True
        return self._read(tuple(_parts(node)))

    def _read(self, values: tuple[object, ...]) -> bool:
        return any(name in self.names for value in values for name in _names(value))

    def decides(self, node: Node, *, under: bool) -> bool:
        """Return whether a placed node may show or hide the part by a decision."""
        if isinstance(node, TextNode):
            return False
        if isinstance(node, VariableNode):
            return self._variable(node.filter_expression, under=under)
        if isinstance(node, SetSlotTagNode):
            return under and f"{SLOT_KEY_PREFIX}{node.name}" in self.names
        parts = tuple(_parts(node))
        if not isinstance(node, _NAMED) and self._part in parts:
            return True
        return not isinstance(node, _STRUCTURAL) and self._read(parts)

    def _variable(self, expression: FilterExpression, *, under: bool) -> bool:
        if any(name in self.names for name in _arguments(expression)):
            return True
        root = _root(expression)
        if root is None or root not in self.names:
            return False
        filters = (func for func, _args in expression.filters)
        return under or any(func not in _PASSING_FILTERS for func in filters)


def _opaque(node: Node) -> bool:
    """Return whether a node runs code the analysis cannot read."""
    if isinstance(node, TagHelperNode) and node.takes_context:
        return True
    return not type(node).__module__.startswith(_KNOWN_MODULES)


class _Closure(TemplateWalk):
    """Place every node one component template reaches, under a condition or not.

    A walk answers `True` the moment it meets what it cannot follow.
    """

    def __init__(self, manager: "ComponentsManager", anchor: Path) -> None:
        """Resolve nested components through `manager` from the page's `anchor`."""
        super().__init__(manager, anchor)
        self.placed: _Placed = []
        self.stamps: _Read = set()

    @override
    def stops(self, node: Node) -> bool:
        return isinstance(node, HasPermNode) or _opaque(node)

    @override
    def place(self, node: Node, at: Position) -> None:
        self.placed.append((node, at.under))

    @override
    def inner(self, node: Node, at: Position) -> Position:
        if at.under or isinstance(node, _TRANSPARENT):
            return at
        return replace(at, under=True)

    @override
    def source(self, name: str) -> "TemplateSource | None":
        return _template_source(self.manager, name, self.anchor)

    @override
    def lost(self) -> bool:
        return True

    @override
    def read(self, path: Path) -> None:
        self.stamps.add((path, _mtime(path)))


def _template_source(
    manager: "ComponentsManager", name: str, anchor: Path
) -> "TemplateSource | None":
    """Return the template of a component only a template renders, else `None`.

    A `component.py` decides in code the analysis does not read, so it counts as one.
    """
    info = manager.get_component(name, anchor)
    if info is None or info.module_path is not None:
        return None
    return manager.template_loader.load_source(info)


class Components:
    """Tell which components of one page guard a part of their body, from its anchor.

    A `{% has_perm %}`, a decision around it, a `component.py` or a blind include count.
    """

    def __init__(self, manager: "ComponentsManager", page_path: Path) -> None:
        """Resolve components through `manager` from the template of `page_path`."""
        self._manager = manager
        self._anchor = page_template_path(page_path)
        self._names: dict[tuple[str, str], bool] = {}
        self._stamps: _Read = set()

    @property
    def stamps(self) -> Stamps:
        """Return every file the answers so far read, with its modification time."""
        return frozenset(self._stamps)

    def guarding(self, name: str, part: str = _BODY) -> bool:
        """Return whether the component `name` renders a part behind a decision.

        The part is `children` or the `slot_<name>` variable of one slot.
        """
        held = self._names.get((name, part))
        if held is None:
            held = self._names[name, part] = self._component(name, part)
        return held

    def _component(self, name: str, part: str) -> bool:
        source = _template_source(self._manager, name, self._anchor)
        if source is None:
            return True
        key = (self._anchor, source.path, source.text, part)
        verdict = _verdicts.get(key)
        if verdict is None or (template_edits_watched() and not fresh(verdict.stamps)):
            closure = _Closure(self._manager, self._anchor)
            guarding = closure.component(source, Position())
            if not guarding:
                body = _Body(closure.placed, part)
                guarding = any(
                    body.decides(node, under=under) for node, under in closure.placed
                )
            verdict = _verdicts[key] = _Verdict(guarding, frozenset(closure.stamps))
        self._stamps.update(verdict.stamps)
        return verdict.guarding


type _Holders = tuple[tuple[ComponentTagNode, str], ...]


def guarded_zones(
    nodelist: NodeList, components: Components
) -> Iterator[tuple[str, str]]:
    """Yield each zone a guarding component holds once, with the outermost such one."""
    seen: set[str] = set()
    for zone, holders in _held_zones(nodelist, ()):
        if zone in seen:
            continue
        guard = next(
            (tag.name for tag, part in holders if components.guarding(tag.name, part)),
            None,
        )
        if guard is not None:
            seen.add(zone)
            yield zone, guard


def _held_zones(
    nodelist: NodeList, holders: _Holders
) -> Iterator[tuple[str, _Holders]]:
    """Yield each zone with the component tags around it and the part it fills."""
    for node in nodelist:
        inner = holders
        if isinstance(node, ZoneNode) and holders:
            yield node.name, holders
        if isinstance(node, ComponentTagNode):
            inner = (*holders, (node, _BODY))
        elif isinstance(node, SlotTagNode):
            part = f"{SLOT_KEY_PREFIX}{node.name}"
            inner = tuple(
                (tag, part if tag is node.owner else held) for tag, held in holders
            )
        for child in child_nodelists(node):
            yield from _held_zones(child, inner)


def opaque_zones(
    nodelist: NodeList, tag: Node | None = None
) -> Iterator[tuple[str, Node]]:
    """Yield each zone under a block tag the analysis cannot read, with the outermost.

    A tag of a module outside `django.` and `next.` may hide its body in code.
    """
    for node in nodelist:
        if isinstance(node, ZoneNode) and tag is not None:
            yield node.name, tag
        held = tag
        if held is None and not isinstance(node, _TRANSPARENT) and _opaque(node):
            held = node
        for child in child_nodelists(node):
            yield from opaque_zones(child, held)


def bound_zones(nodelist: NodeList, components: Components) -> frozenset[str]:
    """Return the zones a standalone render must refuse, in a guard or a condition.

    A condition or a tag the analysis cannot read above a zone decides like a component.
    """
    guarded = {name for name, _ in guarded_zones(nodelist, components)}
    guarded.update(name for name, _ in opaque_zones(nodelist))
    return frozenset(guarded.union(zones_under(nodelist, _CONDITIONS)))


@dataclass(frozen=True, slots=True)
class _Branch:
    """One `{% has_perm %}` above a zone and whether the zone is in its first branch."""

    node: HasPermNode
    granted: bool

    def holds(self, context: Context) -> bool:
        """Return whether the render takes the branch the zone sits in."""
        return self.node.granted(context) is self.granted


type _Step = _Branch | WithNode


def _user_only(step: _Step) -> bool:
    """Return whether a step asks the user for a permission named by a string."""
    if not isinstance(step, _Branch):
        return False
    return step.node.obj is None and step.node.literal_perm() is not None


def _has_user(request: "HttpRequest") -> bool:
    return getattr(request, "user", None) is not None


@dataclass(frozen=True, slots=True)
class Recheck:
    """The `{% has_perm %}` blocks above one zone, asked again before it renders alone.

    A block asking the user a permission named by a string runs before the page context.
    """

    steps: tuple[_Step, ...]
    early: tuple[_Branch, ...]
    late: tuple[_Step, ...]

    def refuses_user(self, request: "HttpRequest") -> bool:
        """Return whether a block that asks only the user refuses the request."""
        if not self.early or not _has_user(request):
            return False
        context = Context({REQUEST_KEY: request})
        return not all(branch.holds(context) for branch in self.early)

    def refuses(self, context: Context, request: "HttpRequest") -> bool:
        """Return whether a block above the zone refuses the render context.

        The `{% with %}` bindings above a block are pushed for it as a full render does.
        """
        steps = self.late if _has_user(request) else self.steps
        with ExitStack() as stack:
            for step in steps:
                if isinstance(step, WithNode):
                    values = {
                        key: value.resolve(context)
                        for key, value in step.extra_context.items()
                    }
                    stack.enter_context(context.push(values))
                elif not step.holds(context):
                    return True
        return False


def _trimmed(steps: tuple[_Step, ...]) -> tuple[_Step, ...]:
    """Return the steps up to the last block, a binding below it changes nothing."""
    last = max(
        (at for at, step in enumerate(steps) if isinstance(step, _Branch)), default=-1
    )
    return steps[: last + 1]


def _recheck(chain: tuple[_Step, ...]) -> Recheck | None:
    steps = _trimmed(chain)
    if not steps:
        return None
    early = tuple(
        step for step in steps if isinstance(step, _Branch) and _user_only(step)
    )
    late = _trimmed(tuple(step for step in steps if not _user_only(step)))
    return Recheck(steps, early, late)


def rechecks(nodelist: NodeList) -> dict[str, Recheck]:
    """Return the blocks to ask again for each zone a `{% has_perm %}` holds.

    The last zone of a repeated name wins, as it does in the zone registry.
    """
    found: dict[str, Recheck | None] = {}
    _collect(nodelist, (), found)
    return {name: recheck for name, recheck in found.items() if recheck is not None}


def _collect(
    nodelist: NodeList, chain: tuple[_Step, ...], found: dict[str, Recheck | None]
) -> None:
    for node in nodelist:
        if isinstance(node, ZoneNode):
            found[node.name] = _recheck(chain)
        if isinstance(node, HasPermNode):
            _collect(node.nodelist_true, (*chain, _Branch(node, granted=True)), found)
            _collect(node.nodelist_false, (*chain, _Branch(node, granted=False)), found)
            continue
        inner = (*chain, node) if isinstance(node, WithNode) else chain
        for child in child_nodelists(node):
            _collect(child, inner, found)


__all__ = [
    "Components",
    "Recheck",
    "Stamps",
    "bound_zones",
    "fresh",
    "guarded_zones",
    "opaque_zones",
    "rechecks",
]
