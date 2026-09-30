"""The base of the node `{% component %}` compiles to, apart from the tag library.

The tag library reaches `next.static`, which imports this package, so a check finds
compiled component tags through this base instead of importing the library.
"""

from django.template.base import Node


class ComponentTagNode(Node):
    """A compiled `{% component %}` tag, naming the component it renders."""

    name: str


__all__ = ["ComponentTagNode"]
