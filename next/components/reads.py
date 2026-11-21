"""The top-level variables a template reads, which the declared-props check compares."""

from collections.abc import Iterator
from typing import Final

from django.template.base import FilterExpression, Node, NodeList, Variable
from django.template.defaulttags import ForNode, WithNode
from django.template.smartif import TokenBase

from next.interpolation import Interpolation
from next.utils import child_nodelists


_BINDING_ATTRS: Final = frozenset({"asvar", "var_name", "target_var", "variable_name"})
_OPERANDS: Final = ("first", "second", "value")
_LOOP: Final = "forloop"


def _root(value: object) -> str | None:
    if isinstance(value, FilterExpression):
        value = value.var
    if isinstance(value, Variable) and value.lookups:
        return str(value.lookups[0])
    return None


def _names(value: object) -> Iterator[str]:
    """Yield the first lookup of every variable a value of a node reads."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Node | NodeList | str):
            continue
        if isinstance(item, Interpolation):
            pending.extend(item.parts)
        elif isinstance(item, FilterExpression | Variable):
            root = _root(item)
            if root is not None:
                yield root
            if isinstance(item, FilterExpression):
                pending.extend(arg for _func, args in item.filters for _l, arg in args)
        elif isinstance(item, TokenBase):
            pending.extend(getattr(item, name, None) for name in _OPERANDS)
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)


def _own(node: Node) -> Iterator[object]:
    """Yield every value `node` holds outside its child node lists."""
    lists = set(node.child_nodelists)
    for name, value in vars(node).items():
        if name not in lists and name not in {"token", "origin"}:
            yield value


def _bound(node: Node) -> Iterator[str]:
    """Yield the names a tag assigns with `as name`, visible from then on."""
    for name in _BINDING_ATTRS:
        value = getattr(node, name, None)
        if isinstance(value, str) and value:
            yield value


def _inner(node: Node, scope: frozenset[str]) -> frozenset[str]:
    """Return the names the child node lists of `node` see bound."""
    if isinstance(node, ForNode):
        return scope | {*node.loopvars, _LOOP}
    if isinstance(node, WithNode):
        return scope | {str(key) for key in node.extra_context}
    return scope


def reads(nodelist: NodeList) -> tuple[frozenset[str], frozenset[str]]:
    """Return the names a template reads unbound and the names its tags assign."""
    read: set[str] = set()
    assigned: set[str] = set()
    pending: list[tuple[NodeList, frozenset[str]]] = [(nodelist, frozenset())]
    while pending:
        nodes, scope = pending.pop()
        for node in nodes:
            assigned.update(_bound(node))
            for value in _own(node):
                read.update(name for name in _names(value) if name not in scope)
            inner = _inner(node, scope)
            pending.extend((child, inner) for child in child_nodelists(node))
    return frozenset(read), frozenset(assigned)


__all__ = ["reads"]
