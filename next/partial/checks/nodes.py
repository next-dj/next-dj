"""Node-tree walks shared by the zone and form checks of a composed page.

Django declares child node lists on `child_nodelists`, so every walk below descends it.
"""

from typing import TYPE_CHECKING, cast

from django.template.base import Node, NodeList, TextNode
from django.template.defaulttags import ForNode, WithNode

from next.forms.nodes import FormNode
from next.partial.zone import ZoneNode, child_nodelists


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.base import Template


def zone_nodes(template: "Template") -> list[ZoneNode]:
    """Return every zone node of a compiled template."""
    return cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))


def significant(nodelist: NodeList) -> list[Node]:
    """Return the nodes of a list that are not pure whitespace text."""
    out: list[Node] = []
    for node in nodelist:
        if isinstance(node, TextNode) and not node.s.strip():
            continue
        out.append(node)
    return out


def zones_directly_in_with(nodelist: NodeList) -> "Iterator[str]":
    """Yield zone names that are direct children of a `{% with %}` block."""
    for node in nodelist:
        if isinstance(node, WithNode):
            for child in significant(node.nodelist):
                if isinstance(child, ZoneNode):
                    yield child.name
        for child_list in child_nodelists(node):
            yield from zones_directly_in_with(child_list)


def forms_in_loop(nodelist: NodeList, *, inside: bool = False) -> "Iterator[FormNode]":
    """Yield each `{% form %}` node reached while a `{% for %}` is on the path."""
    for node in nodelist:
        if isinstance(node, FormNode) and inside:
            yield node
        now_inside = inside or isinstance(node, ForNode)
        for child in child_nodelists(node):
            yield from forms_in_loop(child, inside=now_inside)


__all__ = ["forms_in_loop", "significant", "zone_nodes", "zones_directly_in_with"]
