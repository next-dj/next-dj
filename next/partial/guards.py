"""The zones of a composed page a standalone render would show without the check above.

The `next.E163` check and the zone registry read this one analysis.
"""

from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from django.template import Engine, Template, TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import FilterExpression, Node, NodeList
from django.template.defaulttags import ForNode, IfNode
from django.template.loader_tags import ExtendsNode, IncludeNode

from next.caches import BoundedCache
from next.components.nodes import ComponentTagNode
from next.pages.paths import page_template_path
from next.permissions.nodes import HasPermNode
from next.utils import template_edits_watched

from .zone import ZoneNode, zones_under


if TYPE_CHECKING:
    from next.components.manager import ComponentsManager


type Stamps = frozenset[tuple[Path, float]]
"""The files an answer read, each with the modification time it was read at."""

type _Read = set[tuple[Path, float]]

_CONDITIONS: Final = (IfNode, ForNode)


@dataclass(frozen=True, slots=True)
class _Verdict:
    guarding: bool
    stamps: Stamps


_verdicts: BoundedCache[tuple[Path, str], _Verdict] = BoundedCache()


def fresh(stamps: Stamps) -> bool:
    """Return whether every file an answer read is unchanged since it was read."""
    return all(_mtime(path) == mtime for path, mtime in stamps)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _literal(expression: object) -> str | None:
    """Return the name an expression spells as a string, `None` for anything else."""
    if not isinstance(expression, FilterExpression):
        return None
    name = expression.var
    if expression.filters or not isinstance(name, str):
        return None
    return str(name)


class Components:
    """Tell which components of one page guard their body, from the page's anchor.

    A `{% has_perm %}` or an unfollowable reference its template reaches counts.
    """

    def __init__(self, manager: "ComponentsManager", page_path: Path) -> None:
        """Resolve components through `manager` from the template of `page_path`."""
        self._manager = manager
        self._anchor = page_template_path(page_path)
        self._names: dict[str, bool] = {}
        self._stamps: _Read = set()

    @property
    def stamps(self) -> Stamps:
        """Return every file the answers so far read, with its modification time."""
        return frozenset(self._stamps)

    def guarding(self, name: str) -> bool:
        """Return whether the component `name` renders its body behind a check."""
        held = self._names.get(name)
        if held is None:
            held = self._names[name] = self._component(name, set())
        return held

    def _component(self, name: str, seen: set[object]) -> bool:
        info = self._manager.get_component(name, self._anchor)
        if info is None:
            return True
        source = self._manager.template_loader.load_source(info)
        if source is None:
            return False
        key = (self._anchor, source.text)
        if key in seen:
            return False
        verdict = _verdicts.get(key)
        if verdict is None or (template_edits_watched() and not fresh(verdict.stamps)):
            stamps: _Read = {(source.path, _mtime(source.path))}
            guarding = self._text(source.text, {*seen, key}, stamps)
            verdict = _verdicts[key] = _Verdict(guarding, frozenset(stamps))
        self._stamps.update(verdict.stamps)
        return verdict.guarding

    def _text(self, text: str, seen: set[object], stamps: _Read) -> bool:
        try:
            compiled = Template(text)
        except TemplateSyntaxError:
            return True
        return self._nodes(compiled.nodelist, seen, stamps)

    def _nodes(self, nodelist: NodeList, seen: set[object], stamps: _Read) -> bool:
        for node in nodelist.get_nodes_by_type(Node):
            if isinstance(node, HasPermNode):
                return True
            if isinstance(node, ComponentTagNode) and self._component(node.name, seen):
                return True
            if isinstance(node, ExtendsNode) and self._named(
                node.parent_name, seen, stamps
            ):
                return True
            if isinstance(node, IncludeNode) and self._named(
                node.template, seen, stamps
            ):
                return True
        return False

    def _named(self, expression: object, seen: set[object], stamps: _Read) -> bool:
        """Follow a template named by a string, guarding when it cannot be followed."""
        name = _literal(expression)
        if name is None:
            return True
        if name in seen:
            return False
        try:
            template = Engine.get_default().get_template(name)
        except (TemplateDoesNotExist, TemplateSyntaxError):
            return True
        with suppress(OSError, ValueError):
            origin = Path(template.origin.name)
            if origin.is_file():
                stamps.add((origin, _mtime(origin)))
        return self._nodes(template.nodelist, {*seen, name}, stamps)


def guarded_zones(
    nodelist: NodeList, components: Components
) -> Iterator[tuple[str, str | None]]:
    """Yield each zone a `{% has_perm %}` guards once, with the component guarding it.

    The component is `None` for a block in the page itself.
    """
    seen: set[str] = set()
    for name in zones_under(nodelist, HasPermNode):
        if name not in seen:
            seen.add(name)
            yield name, None
    tags = cast("list[ComponentTagNode]", nodelist.get_nodes_by_type(ComponentTagNode))
    for tag in tags:
        if not components.guarding(tag.name):
            continue
        zones = cast("list[ZoneNode]", tag.nodelist.get_nodes_by_type(ZoneNode))
        for zone in zones:
            if zone.name not in seen:
                seen.add(zone.name)
                yield zone.name, tag.name


def bound_zones(nodelist: NodeList, components: Components) -> frozenset[str]:
    """Return the zones a standalone render must refuse, under a check or a condition.

    An `{% if %}` or a `{% for %}` above a zone decides like a permission does.
    """
    guarded = {name for name, _ in guarded_zones(nodelist, components)}
    return frozenset(guarded.union(zones_under(nodelist, _CONDITIONS)))


__all__ = ["Components", "Stamps", "bound_zones", "fresh", "guarded_zones"]
