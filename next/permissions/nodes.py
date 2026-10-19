"""The node `{% has_perm %}` compiles to, a branch on `user.has_perm()`."""

from typing import override

from django.template import Context
from django.template.base import FilterExpression, Node, NodeList

from next.seeding import REQUEST_KEY

from .access import holds


def _user(context: Context) -> object:
    """Return the user of the render, read off its request before the context.

    A zone or a component renders over a plain `Context` holding the request as a key.
    """
    request = getattr(context, "request", None)
    if request is None:
        request = context.get(REQUEST_KEY)
    user = getattr(request, "user", None)
    return context.get("user") if user is None else user


class HasPermNode(Node):
    """Render the first branch for a user who holds the permission, else the second."""

    child_nodelists = ("nodelist_true", "nodelist_false")

    def __init__(
        self,
        perm: FilterExpression,
        obj: FilterExpression | None,
        nodelist_true: NodeList,
        nodelist_false: NodeList,
    ) -> None:
        """Store the permission and object expressions and both branches."""
        self.perm = perm
        self.obj = obj
        self.nodelist_true = nodelist_true
        self.nodelist_false = nodelist_false

    def literal_perm(self) -> str | None:
        """Return the permission the tag names by a string, `None` for a variable."""
        name = self.perm.var
        if self.perm.filters or not isinstance(name, str):
            return None
        return str(name)

    def granted(self, context: Context) -> bool:
        """Return whether the user of the render holds the permission.

        An object that resolves to nothing denies, so it never asks the model question.
        """
        perm = self.perm.resolve(context, ignore_failures=True)
        if not isinstance(perm, str) or not perm:
            return False
        user = _user(context)
        if self.obj is None:
            return holds(user, (perm,))
        return holds(user, (perm,), self.obj.resolve(context, ignore_failures=True))

    @override
    def render(self, context: Context) -> str:
        """Render the branch the permission of the user selects."""
        branch = self.nodelist_true if self.granted(context) else self.nodelist_false
        return branch.render(context)


__all__ = ["HasPermNode"]
