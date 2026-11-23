"""The node `{% has_perm %}` compiles to, and the granting branches open around a call.

A token re-checks on reload what those branches name, at compile time and at render.
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import override
from weakref import WeakKeyDictionary

from django.template import Context
from django.template.base import FilterExpression, Node, NodeList, Parser

from next.seeding import REQUEST_KEY

from .access import holds


type Guards = tuple[str | None, ...]
"""The granting branches around a call by the permission each names, `None` unnamed."""

_granting: WeakKeyDictionary[Parser, list[str | None]] = WeakKeyDictionary()
_rendering: ContextVar[Guards] = ContextVar("next_granting_branches", default=())


def _literal(perm: FilterExpression) -> str | None:
    """Return the permission `perm` names by a string, `None` for a variable."""
    name = perm.var
    if perm.filters or not isinstance(name, str):
        return None
    return str(name)


def _claim(perm: FilterExpression, obj: FilterExpression | None) -> str | None:
    """Return what a granting branch names, `None` for a variable or an object."""
    return _literal(perm) if obj is None else None


def _joined(guards: Iterable[str | None]) -> tuple[str, ...] | None:
    """Return the names of `guards` once each, `None` when one names nothing."""
    held = tuple(guards)
    named = [perm for perm in held if perm is not None]
    if len(named) != len(held):
        return None
    return tuple(dict.fromkeys(named))


@contextmanager
def granting(
    parser: Parser, perm: FilterExpression, obj: FilterExpression | None
) -> Generator[None]:
    """Hold one granting branch open on `parser` while the branch compiles."""
    stack = _granting.setdefault(parser, [])
    stack.append(_claim(perm, obj))
    try:
        yield
    finally:
        stack.pop()


def granted_perms(parser: Parser) -> tuple[str, ...] | None:
    """Return the permissions the granting branches open on `parser` name, if all do."""
    return _joined(_granting.get(parser, ()))


def rendering_guards() -> Guards:
    """Return the granting branches rendering around the current node, outermost first.

    It spans includes, inherited blocks and component templates, which compile apart.
    """
    return _rendering.get()


@contextmanager
def rendered_under(guards: Guards) -> Generator[None]:
    """Render the block under `guards`, as a deferred render runs after they closed."""
    token = _rendering.set(guards)
    try:
        yield
    finally:
        _rendering.reset(token)


def claimed(perms: tuple[str, ...] | None, guards: Guards) -> tuple[str, ...] | None:
    """Return `perms` joined with what `guards` name, `None` when one is unnamed."""
    return None if perms is None else _joined((*perms, *guards))


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
        self.claim = _claim(perm, obj)

    def literal_perm(self) -> str | None:
        """Return the permission the tag names by a string, `None` for a variable."""
        return _literal(self.perm)

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
        """Render the branch the permission of the user selects.

        The granting branch renders with its guard open, for every call inside to claim.
        """
        if not self.granted(context):
            return self.nodelist_false.render(context)
        token = _rendering.set((*_rendering.get(), self.claim))
        try:
            return self.nodelist_true.render(context)
        finally:
            _rendering.reset(token)


__all__ = [
    "Guards",
    "HasPermNode",
    "claimed",
    "granted_perms",
    "granting",
    "rendered_under",
    "rendering_guards",
]
