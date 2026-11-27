"""System check for a root layout whose chrome reads the request path."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Warning as DjangoWarning

from next.checks import NEXT, register
from next.conf.router import router_enabled
from next.pages.checks.reads import chrome_contents
from next.pages.loaders import PAGE_MODULE, layout_chain, template_source
from next.pages.processors import context_processors

from .layouts import layouts


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


_PATH_READS: Final = re.compile(r"\brequest\.(?:path|path_info|get_full_path)\b")
_PATH_SOURCES: Final = ("request.path", "get_full_path", "resolver_match")
_PATH_ATTRS: Final = frozenset(
    {
        "path",
        "path_info",
        "get_full_path",
        "get_full_path_info",
        "build_absolute_uri",
        "resolver_match",
        "META",
    }
)


def _request_name(tree: ast.AST) -> str | None:
    """Return the parameter the request reaches the first function of `tree` through."""
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ),
        None,
    )
    if func is None:
        return None
    positional = [*func.args.posonlyargs, *func.args.args]
    if positional:
        return positional[0].arg
    return None if func.args.vararg is None else func.args.vararg.arg


def _bare(node: ast.AST, request: str | None) -> bool:
    """Whether `node` hands the request on whole or calls a method of it.

    Only a plain attribute such as `request.user` is known to leave the path behind.
    """
    called = {id(child.func) for child in ast.walk(node) if isinstance(child, ast.Call)}
    plain = {
        id(child.value)
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and id(child) not in called
    }
    return any(
        isinstance(child, ast.Name) and child.id == request and id(child) not in plain
        for child in ast.walk(node)
    )


@dataclass(frozen=True, slots=True)
class _Locals:
    """The names a processor binds, by the value each plain assignment gives it.

    A name bound any other way, a loop target or an unpacking, is read as opaque.
    """

    assigned: dict[str, list[ast.expr]]
    opaque: frozenset[str]


def _locals(tree: ast.AST) -> _Locals:
    """Return the plain assignments of `tree` and every name it binds otherwise."""
    assigned: dict[str, list[ast.expr]] = {}
    plain: set[int] = set()
    for node in ast.walk(tree):
        pairs: list[tuple[ast.Name, ast.expr | None]]
        if isinstance(node, ast.Assign):
            pairs = [
                (target, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and isinstance(
            node.target, ast.Name
        ):
            pairs = [(node.target, node.value)]
        else:
            continue
        for target, value in pairs:
            plain.add(id(target))
            if value is not None:
                assigned.setdefault(target.id, []).append(value)
    opaque = frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and id(node) not in plain
    )
    return _Locals(assigned, opaque)


class _PathTaint:
    """Decide which expressions of one processor can change with the request path."""

    def __init__(self, tree: ast.AST) -> None:
        """Read the request parameter, then mark every local the path flows into."""
        self.request = _request_name(tree)
        bound = _locals(tree)
        self.tainted: set[str] = set(bound.opaque)
        grown = True
        while grown:
            grown = False
            for name, values in bound.assigned.items():
                if name not in self.tainted and any(map(self.depends, values)):
                    self.tainted.add(name)
                    grown = True

    def depends(self, node: ast.AST) -> bool:
        """Whether `node` reads a path attribute, a tainted name or the bare request."""
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in _PATH_ATTRS:
                root = _root(child.value)
                if not isinstance(root, ast.Name) or self.request in {None, root.id}:
                    return True
            if isinstance(child, ast.Name) and child.id in self.tainted:
                return True
        return _bare(node, self.request)


def _root(node: ast.expr) -> ast.expr:
    """Return the expression an attribute or subscript chain starts from."""
    while isinstance(node, ast.Attribute | ast.Subscript):
        node = node.value
    return node


def _path_dependent_keys(func: Callable[..., object]) -> frozenset[str]:
    """Return the keys of the dict literals `func` returns whose value reads the path.

    The source is read statically, so a value it cannot follow counts as dependent.
    """
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):
        return frozenset()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    taint = _PathTaint(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            found.update(
                key.value
                for key, value in zip(node.value.keys, node.value.values, strict=True)
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and taint.depends(value)
            )
    return frozenset(found)


def _path_keys() -> frozenset[str]:
    """Return the keys whose value a context processor reading the path derives from it.

    A processor whose source never reads the path is skipped, so `messages` stays out.
    """
    keys: set[str] = set()
    for processor in context_processors():
        try:
            source = inspect.getsource(processor)
        except (OSError, TypeError):
            continue
        if any(needle in source for needle in _PATH_SOURCES):
            keys.update(_path_dependent_keys(processor))
    return frozenset(keys)


def _root_layouts() -> Iterator[Path]:
    """Yield every layout that ends the chain of a page below it."""
    for layout in layouts():
        chain = layout_chain(layout.parent / PAGE_MODULE)
        if chain and chain[-1].resolve() == layout.resolve():
            yield layout


def _path_reads(text: str, keys: frozenset[str]) -> list[str]:
    """Return what a template reads outside its zones that changes with the path."""
    found: dict[str, None] = {}
    pattern = (
        re.compile(rf"\b({'|'.join(map(re.escape, sorted(keys)))})\b") if keys else None
    )
    for contents in chrome_contents(text):
        for match in _PATH_READS.finditer(contents):
            found[match.group(0)] = None
        if pattern is not None:
            for match in pattern.finditer(contents):
                found[match.group(1)] = None
    return list(found)


@register(NEXT)
def check_root_layout_paths(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a root layout whose chrome reads the request path (`next.W176`)."""
    if not router_enabled():
        return []
    keys = _path_keys()
    warnings: list[CheckMessage] = []
    for layout in _root_layouts():
        text = template_source(layout)
        reads = [] if text is None else _path_reads(text, keys)
        if reads:
            warnings.append(
                DjangoWarning(
                    f"The root layout {layout} reads {', '.join(reads)} outside a "
                    "zone, so its chrome differs on every page and every navigation "
                    "under it morphs the whole body.",
                    hint="Move the value into a {% zone %} or into the page.",
                    obj=str(layout),
                    id="next.W176",
                )
            )
    return warnings


__all__ = ["check_root_layout_paths"]
