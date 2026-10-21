"""The base classes of the nodes `{% component %}` and its slot tags compile to.

A check finds compiled tags through them, since the library imports `next.static`.
"""

from django.template.base import Node, NodeList


class ComponentTagNode(Node):
    """A compiled `{% component %}` tag, naming the component it renders."""

    name: str
    nodelist: NodeList


class SlotTagNode(Node):
    """A compiled `{% slot %}` body, filling a slot of the component that owns it."""

    name: str
    nodelist: NodeList
    owner: ComponentTagNode | None


class SetSlotTagNode(Node):
    """A compiled `{% set_slot %}`, where a component template renders a slot."""

    name: str
    nodelist: NodeList


__all__ = ["ComponentTagNode", "SetSlotTagNode", "SlotTagNode"]
