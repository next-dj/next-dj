"""The base class of the node `{% component %}` compiles to.

The tag library imports `next.static`, which imports this package, so a check finds
compiled component tags through this base class instead of importing the library.
"""

from django.template.base import Node


class ComponentTagNode(Node):
    """A compiled `{% component %}` tag, naming the component it renders."""

    name: str


__all__ = ["ComponentTagNode"]
