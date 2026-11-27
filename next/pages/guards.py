"""The static scan for a `@context` callable that answers 404, 403 or 400."""

from __future__ import annotations

import ast
import builtins
import functools
import inspect
import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404
from django.shortcuts import get_list_or_404, get_object_or_404

from next.caches import BoundedCache
from next.deps.introspect import cached_signature
from next.deps.markers import Depends
from next.deps.objects import names_object

from .context import Context


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import FunctionType


GUARD_CALLS: Final[tuple[Callable[..., object], ...]] = (
    get_object_or_404,
    get_list_or_404,
)
"""The functions whose call answers a 404 from inside a callable."""

GUARD_RAISES: Final[tuple[type[Exception], ...]] = (
    Http404,
    PermissionDenied,
    BadRequest,
    SuspiciousOperation,
)
"""The exceptions whose `raise`, a subclass included, answers a 404, a 403 or a 400."""

DEPTH: Final = 3
"""How many calls deep the scan follows the functions a callable calls."""

_NAMED: Final = re.compile(
    r"\b(?:"
    + "|".join(
        sorted(
            [
                *(call.__name__ for call in GUARD_CALLS),
                *(error.__name__ for error in GUARD_RAISES),
            ]
        )
    )
    + r")\b"
)


@dataclass(frozen=True, slots=True)
class Scan:
    """Whether a callable guards, and the functions past `DEPTH` it could not follow."""

    guards: bool
    hidden: tuple[str, ...] = ()


_NO_GUARD: Final = Scan(guards=False)
_SCANNED: BoundedCache[object, Scan] = BoundedCache(depends=())


def _layers(func: object) -> Iterator[FunctionType]:
    """Yield the functions behind `func`, past a partial, each wrapper first."""
    while isinstance(func, functools.partial):
        func = func.func
    met: set[object] = set()
    found = _function(func)
    while found is not None and found not in met:
        met.add(found)
        yield found
        found = _function(getattr(found, "__wrapped__", None))


def _function(found: object) -> FunctionType | None:
    """Return the Python function `found` names, a method's own, `None` for another."""
    if isinstance(found, (staticmethod, classmethod)) or inspect.ismethod(found):
        found = found.__func__
    return found if inspect.isfunction(found) else None


def _tree(func: FunctionType) -> ast.AST | None:
    """Return the syntax tree of the source of `func`, `None` without a readable one."""
    try:
        source = inspect.getsource(func.__code__)
    except (OSError, TypeError):
        return None
    try:
        return ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None


def _named(func: FunctionType, name: str) -> object:
    """Return what `name` names in `func`, its closure first, then its globals."""
    code = func.__code__
    if name in code.co_freevars:
        cell = (func.__closure__ or ())[code.co_freevars.index(name)]
        try:
            return cell.cell_contents
        except ValueError:
            return None
    if name in func.__globals__:
        return func.__globals__[name]
    return getattr(builtins, name, None)


def _resolved(node: ast.expr, func: FunctionType) -> object:
    """Return what a name or a dotted attribute names in `func`, else `None`.

    Only a module or a class is looked into, statically, so no property runs.
    """
    if isinstance(node, ast.Name):
        return _named(func, node.id)
    if isinstance(node, ast.Attribute):
        owner = _resolved(node.value, func)
        if inspect.ismodule(owner) or isinstance(owner, type):
            return inspect.getattr_static(owner, node.attr, None)
    return None


def _raised(node: ast.Raise, func: FunctionType) -> bool:
    """Return whether `node` raises a guard exception or one of its subclasses."""
    exc = node.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    found = None if exc is None else _resolved(exc, func)
    return isinstance(found, type) and issubclass(found, GUARD_RAISES)


def _body(func: FunctionType) -> tuple[bool, list[FunctionType]]:
    """Return whether the body of `func` guards itself, and the functions it calls."""
    tree = _tree(func)
    called: list[FunctionType] = []
    for node in () if tree is None else ast.walk(tree):
        if isinstance(node, ast.Raise) and _raised(node, func):
            return True, called
        if isinstance(node, ast.Call):
            found = _resolved(node.func, func)
            if any(found is guard for guard in GUARD_CALLS):
                return True, called
            called.extend(_layers(found))
    return False, called


def _walk(func: FunctionType, depth: int, seen: set[object], hidden: list[str]) -> bool:
    """Return whether `func` or a function it calls, `DEPTH` calls deep, guards."""
    seen.add(func)
    guards, called = _body(func)
    if guards:
        return True
    for helper in called:
        if helper in seen:
            continue
        if depth >= DEPTH:
            seen.add(helper)
            if _mentions(helper):
                hidden.append(helper.__qualname__)
        elif _walk(helper, depth + 1, seen, hidden):
            return True
    return False


def _factory(default: object) -> Callable[..., object] | None:
    """Return the callable a `Depends` or a `Context` default resolves through."""
    if isinstance(default, Depends):
        found = default.dependency
    elif isinstance(default, Context):
        found = default.source
    else:
        return None
    return found if callable(found) else None


def _injected(
    func: Callable[..., object],
    resolved: set[object],
    seen: set[object],
    hidden: list[str],
) -> bool:
    """Return whether the resolver fills a parameter of `func` with a 404 by contract.

    A `DObject` row answers 404 when it is missing or hidden, and a factory runs first.
    """
    try:
        parameters = cached_signature(func).parameters.values()
    except (ValueError, TypeError):
        return False
    for param in parameters:
        if names_object(param.annotation):
            return True
        factory = _factory(param.default)
        if factory is not None and factory not in resolved:
            resolved.add(factory)
            if _settles(factory, resolved, seen, hidden):
                return True
    return False


def _settles(
    func: Callable[..., object],
    resolved: set[object],
    seen: set[object],
    hidden: list[str],
) -> bool:
    """Return whether resolving the parameters of `func` or running it guards."""
    return _injected(func, resolved, seen, hidden) or any(
        layer not in seen and _walk(layer, 0, seen, hidden) for layer in _layers(func)
    )


def _scan(func: Callable[..., object]) -> Scan:
    """Return the scan of `func`, read once per outermost function object."""
    layers = tuple(_layers(func))
    if not layers:
        return _NO_GUARD
    held = _SCANNED.get(layers[0])
    if held is not None:
        return held
    seen: set[object] = set()
    hidden: list[str] = []
    found = _settles(func, set(), seen, hidden)
    scan = Scan(guards=found, hidden=() if found else tuple(dict.fromkeys(hidden)))
    _SCANNED[layers[0]] = scan
    return scan


def scans_as_guard(func: Callable[..., object]) -> bool:
    """Return whether `func` answers 404, 403 or 400 itself, by a parameter or a call.

    Names resolve against the globals and closure of each function the scan reads.
    """
    return _scan(func).guards


def _mentions(func: FunctionType) -> bool:
    """Return whether the source of a function names a guard call or exception."""
    try:
        source = inspect.getsource(func.__code__)
    except (OSError, TypeError):
        return False
    return _NAMED.search(source) is not None


def hidden_guards(func: Callable[..., object]) -> tuple[str, ...]:
    """Return the functions past `DEPTH` whose source names a guard the scan missed."""
    return _scan(func).hidden


__all__ = [
    "DEPTH",
    "GUARD_CALLS",
    "GUARD_RAISES",
    "Scan",
    "hidden_guards",
    "scans_as_guard",
]
