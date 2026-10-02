"""The base of the node `{% #consented %}` compiles to, apart from the tag library.

The tag library imports this package, so a check finds the blocks through this base.
"""

from django.template.base import FilterExpression, Node


class ConsentedTagNode(Node):
    """A compiled `{% #consented %}` block, both branches searched by the checks."""

    category: FilterExpression
    child_nodelists = ("granted", "denied")

    def literal_category(self) -> str | None:
        """Return the category the block names by a string, `None` for a variable."""
        name = self.category.var
        if self.category.filters or not isinstance(name, str):
            return None
        return str(name)


__all__ = ["ConsentedTagNode"]
