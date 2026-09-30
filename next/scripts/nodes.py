"""The base of the node `{% #consented %}` compiles to, apart from the tag library.

The tag library imports this package, so a check finds the blocks through this base.
"""

from django.template.base import Node


class ConsentedTagNode(Node):
    """A compiled `{% #consented %}` block."""


__all__ = ["ConsentedTagNode"]
