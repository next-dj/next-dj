import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from next.requests import NEXT_ATTR, RequestNext
from tests.support.imports import module_files, package_root


_REQUESTS = frozenset({"request", "visit", "req"})
_MAKERS = frozenset({"next_of", "held_next", "root_of"})
_DICTS = frozenset({"vars", "instance_dict"})
_DICT_STORES = frozenset({"setdefault", "pop", "update", "popitem", "clear"})
_SLOTS = frozenset(RequestNext.__slots__)
_TYPED = re.compile(rf"\b{RequestNext.__name__}\b")


class How(StrEnum):
    """The spelling a store on a request takes."""

    SETATTR = "setattr"
    DELATTR = "delattr"
    DICT = "dict"
    ATTRIBUTE = "attribute"


@dataclass(frozen=True, slots=True)
class Store:
    """One write of a name onto a request itself."""

    file: str
    line: int
    attribute: str
    how: How

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.attribute)

    def describe(self) -> str:
        return f"{self.file}:{self.line} stores `{self.attribute}` by {self.how}"


@dataclass(frozen=True, slots=True)
class Access:
    """One read or write of a slot of `request.next` through its accessors."""

    file: str
    line: int
    slot: str
    write: bool

    def describe(self) -> str:
        verb = "writes" if self.write else "reads"
        return f"{self.file}:{self.line} {verb} the `{self.slot}` slot"


@dataclass(frozen=True, slots=True)
class Reach:
    """One read of `request.next` that bypasses `next_of` and `held_next`."""

    file: str
    line: int
    spelling: str

    def describe(self) -> str:
        return f"{self.file}:{self.line} reaches request.next by `{self.spelling}`"


def is_request(node: ast.expr) -> bool:
    """Tell whether an expression is named request, visit or req, underscore aside."""
    if isinstance(node, ast.Name):
        return node.id.lstrip("_") in _REQUESTS
    if isinstance(node, ast.Attribute):
        return node.attr.lstrip("_") in _REQUESTS
    return False


def _named(node: ast.expr) -> str:
    """Return the attribute name an expression spells, any `NEXT_ATTR` resolved."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    spelled = node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")
    if spelled.endswith("NEXT_ATTR"):
        return NEXT_ATTR
    return ast.unparse(node)


def _dict_owner(node: ast.expr) -> ast.expr | None:
    """Return `x` of `x.__dict__`, `vars(x)` or `instance_dict(x)`, else `None`."""
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _DICTS
        and node.args
    ):
        return node.args[0]
    return None


def _request_dict(node: ast.expr) -> bool:
    owner = _dict_owner(node)
    return owner is not None and is_request(owner)


def _targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return [
            item
            for target in node.targets
            for item in (target.elts if isinstance(target, ast.Tuple) else [target])
        ]
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return [node.target]
    if isinstance(node, ast.Delete):
        return list(node.targets)
    return []


def _call_store(file: str, node: ast.Call) -> Iterator[Store]:
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"setattr", "delattr"}:
        if len(node.args) >= 2 and is_request(node.args[0]):
            how = How.SETATTR if func.id == "setattr" else How.DELATTR
            yield Store(file, node.lineno, _named(node.args[1]), how)
    elif (
        isinstance(func, ast.Attribute)
        and func.attr in _DICT_STORES
        and _request_dict(func.value)
    ):
        name = _named(node.args[0]) if node.args else "*"
        yield Store(file, node.lineno, name, How.DICT)


def _target_store(file: str, target: ast.expr) -> Iterator[Store]:
    if isinstance(target, ast.Attribute) and is_request(target.value):
        yield Store(file, target.lineno, target.attr, How.ATTRIBUTE)
    elif isinstance(target, ast.Subscript) and _request_dict(target.value):
        yield Store(file, target.lineno, _named(target.slice), How.DICT)


def stores(file: str, source: str) -> list[Store]:
    """Return every store of a name onto a request in one module, in source order."""
    found: list[Store] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            found.extend(_call_store(file, node))
        for target in _targets(node):
            found.extend(_target_store(file, target))
    return sorted(found, key=lambda store: store.line)


def _makes(node: ast.expr) -> bool:
    """Tell whether an expression calls `next_of`, `held_next` or `root_of`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _MAKERS
    return isinstance(func, ast.Attribute) and func.attr in _MAKERS


def _annotated(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Name every parameter of a function annotated with `RequestNext`."""
    arguments = function.args
    every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    return {
        argument.arg
        for argument in every
        if argument.annotation is not None
        and _TYPED.search(ast.unparse(argument.annotation)) is not None
    }


def _bound(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Name every local a function binds from an accessor call or a typed parameter."""
    names = _annotated(function)
    for node in ast.walk(function):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr):
            value = node.value
            if value is None or not _makes(value):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return frozenset(names)


def accesses(file: str, source: str) -> list[Access]:
    """Return every access of a slot through a namespace accessor in one module."""
    tree = ast.parse(source)
    scopes: list[tuple[ast.AST, frozenset[str]]] = [(tree, frozenset())]
    scopes.extend(
        (node, _bound(node))
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    found: dict[tuple[int, int], Access] = {}
    for scope, bound in scopes:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Attribute) or node.attr not in _SLOTS:
                continue
            value = node.value
            if _makes(value) or (isinstance(value, ast.Name) and value.id in bound):
                write = isinstance(node.ctx, ast.Store | ast.Del)
                access = Access(file, node.lineno, node.attr, write)
                found[node.lineno, node.col_offset] = access
    return [found[key] for key in sorted(found)]


def _reads_next(node: ast.AST) -> bool:
    """Tell whether a node reads `next` by `getattr`, `hasattr` or off a request."""
    if isinstance(node, ast.Attribute):
        return node.attr == NEXT_ATTR and is_request(node.value)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "hasattr"}
        and len(node.args) >= 2
        and _named(node.args[1]) == NEXT_ATTR
    )


def reaches(file: str, source: str) -> list[Reach]:
    """Return every read of `request.next` by `getattr` or by plain attribute."""
    found = [
        Reach(file, node.lineno, ast.unparse(node))
        for node in ast.walk(ast.parse(source))
        if _reads_next(node)
    ]
    return sorted(found, key=lambda reach: reach.line)


def framework_sources() -> Iterator[tuple[str, str]]:
    """Yield every module of `next/` as its path from the checkout root and source."""
    root = package_root().parent
    for file in module_files().values():
        yield file.relative_to(root).as_posix(), file.read_text(encoding="utf-8")
