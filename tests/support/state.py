import ast
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from tests.support.imports import module_files, package_root


class Rule(StrEnum):
    """The shape of process state a unit takes, which decides what the guard asks."""

    FUNCTOOLS = "functools"
    UNDECLARED = "undeclared"
    CONTAINER = "container"
    HOLDER = "holder"
    GLOBAL = "global"
    VARS = "vars"


_ADVICE: dict[Rule, str] = {
    Rule.FUNCTOOLS: (
        "a bare functools cache. Use `memo(<sources>)` from next.caches when the "
        "value follows settings, routes or a fork, `pure_cache()` when only the "
        "arguments and the code decide it."
    ),
    Rule.UNDECLARED: (
        "a cache without `depends=`. Name the sources it follows, or `depends=()` "
        "when its keys and the stamps its entries carry decide them."
    ),
    Rule.CONTAINER: (
        "a module-level container the module writes to. Hold it in a cache of "
        "next.caches that names its sources, or attach the reset that clears it to "
        "a source with `<SOURCE>.attach(reset)`."
    ),
    Rule.HOLDER: (
        "a module-level instance of a private class the module writes. Hold the value "
        "in `Memo(depends=...)` from next.caches, or attach its reset to a source."
    ),
    Rule.GLOBAL: "a rebound global. Hold the value in `Memo(depends=...)` instead.",
    Rule.VARS: (
        "a bare `vars()` view. Use `instance_dict()` from next.caches, which states "
        "that the owner's resets keep it current."
    ),
}

_FUNCTOOLS = frozenset({"cache", "lru_cache"})
_CACHES = frozenset({"BoundedCache", "PageCache", "LruCache"})
_CONTAINERS = frozenset(
    {
        "dict",
        "list",
        "set",
        "defaultdict",
        "OrderedDict",
        "deque",
        "Counter",
        "WeakSet",
        "WeakValueDictionary",
        "bytearray",
    }
)
_MUTATORS = frozenset(
    {
        "add",
        "append",
        "appendleft",
        "clear",
        "discard",
        "extend",
        "extendleft",
        "insert",
        "move_to_end",
        "pop",
        "popitem",
        "remove",
        "reverse",
        "rotate",
        "setdefault",
        "sort",
        "update",
    }
)
_ATTACHING = frozenset({"attach"})
_LITERALS = (ast.Dict, ast.DictComp, ast.List, ast.ListComp, ast.Set, ast.SetComp)


@dataclass(frozen=True, slots=True)
class Unit:
    """One unit of process state the guard asks about."""

    file: str
    line: int
    name: str
    rule: Rule

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.name)

    def describe(self) -> str:
        return f"{self.file}:{self.line} `{self.name}` is {_ADVICE[self.rule]}"


def _called(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _is_container(value: ast.expr) -> bool:
    if isinstance(value, _LITERALS):
        return True
    return isinstance(value, ast.Call) and _called(value) in _CONTAINERS


def _written(tree: ast.Module) -> frozenset[str]:
    """Name every attribute or name the module writes into, item or attribute."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _MUTATORS:
                found.update(_names(node.func.value))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = (
                node.targets
                if isinstance(node, (ast.Assign, ast.Delete))
                else [node.target]
            )
            for target in targets:
                if isinstance(target, (ast.Subscript, ast.Attribute)):
                    found.update(_names(target.value))
    return frozenset(found)


def _names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    return set()


def _frozen(cls: ast.ClassDef) -> bool:
    for decorator in cls.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                value = keyword.value
                if keyword.arg == "frozen" and isinstance(value, ast.Constant):
                    return bool(value.value)
    return False


def _self_writes(cls: ast.ClassDef) -> bool:
    """Whether a method past the constructor writes an attribute of the instance."""
    for method in cls.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if method.name in {"__init__", "__post_init__"}:
            continue
        for node in ast.walk(method):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    return True
    return False


def _top(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the statements of a module or class body, through `if` and `try`."""
    for statement in body:
        if isinstance(statement, ast.If):
            yield from _top(statement.body)
            yield from _top(statement.orelse)
        elif isinstance(statement, ast.Try):
            yield from _top(statement.body)
            for handler in statement.handlers:
                yield from _top(handler.body)
            yield from _top(statement.orelse)
            yield from _top(statement.finalbody)
        else:
            yield statement


def _assigned(statement: ast.stmt) -> tuple[list[str], ast.expr | None]:
    if isinstance(statement, ast.Assign):
        names = [t.id for t in statement.targets if isinstance(t, ast.Name)]
        return names, statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return [statement.target.id], statement.value
    return [], None


def _attach_targets(tree: ast.Module) -> tuple[list[str], set[str]]:
    """Return the functions and the objects whose resets the module attaches."""
    functions: list[str] = []
    objects: set[str] = set()
    for statement in _top(tree.body):
        call = statement.value if isinstance(statement, ast.Expr) else None
        if not (isinstance(call, ast.Call) and _called(call) in _ATTACHING):
            continue
        for argument in call.args:
            if isinstance(argument, ast.Name):
                functions.append(argument.id)
            elif isinstance(argument, ast.Attribute):
                objects.update(_names(argument.value))
    return functions, objects


def _read_names(function: ast.AST) -> Iterator[str]:
    for node in ast.walk(function):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield from _names(node.value)


def _attached(tree: ast.Module) -> frozenset[str]:
    """Name what the resets a module attaches to a source reach.

    A reset reaches every name its body reads, and every module function it calls.
    """
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending, reached = _attach_targets(tree)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        if name in functions:
            pending.extend(_read_names(functions[name]))
    return frozenset(reached)


class _Module:
    """The process state one module of `next/` holds, read off its syntax tree."""

    def __init__(self, file: str, source: str) -> None:
        self.file = file
        self.tree = ast.parse(source, file)
        self.attached = _attached(self.tree)
        self.written = _written(self.tree) - self.attached
        self.classes = {
            node.name: node for node in self.tree.body if isinstance(node, ast.ClassDef)
        }

    def units(self) -> list[Unit]:
        found = list(self._body(self.tree.body, "", module=True))
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.extend(
                    Unit(self.file, node.lineno, node.name, Rule.FUNCTOOLS)
                    for decorator in node.decorator_list
                    if _called(decorator) in _FUNCTOOLS
                )
            elif isinstance(node, ast.Global):
                found.extend(
                    Unit(self.file, node.lineno, name, Rule.GLOBAL)
                    for name in node.names
                )
        return sorted(found, key=lambda unit: (unit.line, unit.name))

    def _body(
        self, statements: list[ast.stmt], scope: str, *, module: bool
    ) -> Iterator[Unit]:
        for statement in _top(statements):
            names, value = _assigned(statement)
            for name in names if value is not None else ():
                rule = self._rule(name, value, module=module)
                if rule is not None:
                    yield Unit(self.file, statement.lineno, scope + name, rule)
            if isinstance(statement, ast.ClassDef):
                inner = f"{scope}{statement.name}."
                yield from self._body(statement.body, inner, module=False)

    def _rule(self, name: str, value: ast.expr | None, *, module: bool) -> Rule | None:
        if not isinstance(value, ast.Call):
            return (
                Rule.CONTAINER
                if _is_container(value) and name in self.written
                else None
            )
        called = _called(value)
        if called in _CACHES:
            declared = any(keyword.arg == "depends" for keyword in value.keywords)
            return None if declared else Rule.UNDECLARED
        if _is_container(value):
            return Rule.CONTAINER if name in self.written else None
        if module and called == "vars":
            return Rule.VARS
        return Rule.HOLDER if module and self._holds(name, called) else None

    def _holds(self, name: str, called: str | None) -> bool:
        cls = self.classes.get(called or "")
        return (
            cls is not None
            and cls.name.startswith("_")
            and not _frozen(cls)
            and name not in self.attached
            and (name in self.written or _self_writes(cls))
        )


def scan(file: str, source: str) -> list[Unit]:
    """Return every unit of process state in one module of `next/`.

    A container or holder that a reset attached to a source reaches is declared.
    """
    return _Module(file, source).units()


def framework_state() -> list[Unit]:
    """Return every unit of process state across `next/`."""
    root = package_root().parent
    units: list[Unit] = []
    for file in module_files().values():
        relative = file.relative_to(root).as_posix()
        units.extend(scan(relative, file.read_text(encoding="utf-8")))
    return units
