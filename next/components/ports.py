"""Component tags implementation bound into the `next.ports` slot at app startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

from next.ports import ComponentTags
from next.templatetags.components import ComponentNode


if TYPE_CHECKING:
    from django.template.base import NodeList


class ComponentTagsImpl(ComponentTags):
    """Binds the port to the node the `{% component %}` tag compiles to."""

    @override
    def component_names(self, nodelist: NodeList) -> list[str]:
        """Return the name of every `{% component %}` tag the nodes hold."""
        nodes = cast("list[ComponentNode]", nodelist.get_nodes_by_type(ComponentNode))
        return [node.name for node in nodes]


__all__ = ["ComponentTagsImpl"]
