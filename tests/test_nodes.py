import ast
import importlib.util
import pydoc
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest
from django.template import Node

from tests.support.cases.nodes import (
    NODE_STATE_CLEAN_CASES,
    NODE_STATE_VIOLATION_CASES,
    NodeStateCase,
)


_MUTATORS = frozenset(
    {
        "append",
        "update",
        "setdefault",
        "extend",
        "add",
        "insert",
        "pop",
        "clear",
        "remove",
        "discard",
        "popitem",
        "sort",
        "reverse",
        "move_to_end",
    }
)
_ATTRIBUTE_WRITERS = frozenset({"setattr", "delattr"})
_DUNDER_WRITERS = frozenset({"__setattr__", "__delattr__"})

type _Method = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class _Class:
    """One class definition with the dotted names its bases spell in its module."""

    module: str
    node: ast.ClassDef
    bases: tuple[str, ...]

    def methods(self) -> dict[str, _Method]:
        return {
            statement.name: statement
            for statement in self.node.body
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        }


def _module_names(relative: str) -> tuple[str, str]:
    """Return the dotted name of the module at `relative` and of its package."""
    parts = PurePosixPath(relative).with_suffix("").parts
    if parts[-1] == "__init__":
        module = ".".join(parts[:-1])
        return module, module
    return ".".join(parts), ".".join(parts[:-1])


def _imported_from(package: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    anchor = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
    return ".".join(part for part in (anchor, node.module) if part)


def _aliases(module: str, package: str, tree: ast.Module) -> dict[str, str]:
    """Map every name an import binds in `module` to the dotted name it stands for."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.partition(".")[0]
                bound = alias.name if alias.asname else head
                aliases[f"{module}.{alias.asname or head}"] = bound
        elif isinstance(node, ast.ImportFrom):
            source = _imported_from(package, node)
            for alias in node.names:
                aliases[f"{module}.{alias.asname or alias.name}"] = (
                    f"{source}.{alias.name}"
                )
    return aliases


def _dotted(module: str, expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return f"{module}.{expr.id}"
    if isinstance(expr, ast.Attribute):
        head = _dotted(module, expr.value)
        return None if head is None else f"{head}.{expr.attr}"
    if isinstance(expr, ast.Subscript):
        return _dotted(module, expr.value)
    return None


def _canonical(
    name: str, classes: Mapping[str, _Class], aliases: Mapping[str, str]
) -> str:
    """Follow import aliases until `name` is a scanned class or lies outside them."""
    seen: set[str] = set()
    while name not in seen and name not in classes:
        seen.add(name)
        prefix = name
        while prefix and prefix not in aliases:
            prefix = prefix.rpartition(".")[0]
        if not prefix:
            break
        name = aliases[prefix] + name[len(prefix) :]
    return name


def _is_node(
    name: str,
    classes: Mapping[str, _Class],
    aliases: Mapping[str, str],
    memo: dict[str, bool],
) -> bool:
    name = _canonical(name, classes, aliases)
    if name not in memo:
        memo[name] = False
        found = classes.get(name)
        if found is not None:
            memo[name] = any(
                _is_node(base, classes, aliases, memo) for base in found.bases
            )
        elif name.startswith("django."):
            located = pydoc.locate(name)
            memo[name] = isinstance(located, type) and issubclass(located, Node)
    return memo[name]


def _is_renderable(found: _Class) -> bool:
    """Tell whether the class renders like a template, by `render(self, context)`."""
    render = found.methods().get("render")
    if render is None:
        return False
    arguments = (*render.args.posonlyargs, *render.args.args)
    return [argument.arg for argument in arguments] == ["self", "context"]


def _classes(sources: Mapping[str, str]) -> tuple[dict[str, _Class], dict[str, str]]:
    classes: dict[str, _Class] = {}
    aliases: dict[str, str] = {}
    for relative, text in sources.items():
        module, package = _module_names(relative)
        tree = ast.parse(text)
        aliases.update(_aliases(module, package, tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = (_dotted(module, base) for base in node.bases)
                classes[f"{module}.{node.name}"] = _Class(
                    module, node, tuple(base for base in bases if base is not None)
                )
    return classes, aliases


def _rendering(classes: Mapping[str, _Class], aliases: Mapping[str, str]) -> list[str]:
    memo: dict[str, bool] = {}
    return [
        name
        for name, found in classes.items()
        if _is_node(name, classes, aliases, memo) or _is_renderable(found)
    ]


def _rendering_classes(sources: Mapping[str, str]) -> dict[str, _Class]:
    """Name every `Node` subclass and every template-like renderable of `sources`."""
    classes, aliases = _classes(sources)
    return {name: classes[name] for name in _rendering(classes, aliases)}


def _mro(
    name: str, classes: Mapping[str, _Class], aliases: Mapping[str, str], seen: set[str]
) -> Iterator[_Class]:
    """Yield the class and its scanned bases depth first, the way lookups walk them."""
    if name in seen or name not in classes:
        return
    seen.add(name)
    found = classes[name]
    yield found
    for base in found.bases:
        yield from _mro(_canonical(base, classes, aliases), classes, aliases, seen)


def _lookup(mro: Sequence[_Class], name: str) -> tuple[_Class, _Method] | None:
    for owner in mro:
        method = owner.methods().get(name)
        if method is not None:
            return owner, method
    return None


@dataclass(frozen=True, slots=True)
class _Scope:
    """The local names of a method bound to `self` and to the state `self` holds."""

    selves: frozenset[str]
    aliases: frozenset[str]


def _on_state(expr: ast.expr, scope: _Scope) -> bool:
    """Tell whether `expr` reaches into the state of `self` or of an alias of it."""
    through_attribute = False
    while isinstance(expr, ast.Attribute | ast.Subscript):
        through_attribute = through_attribute or isinstance(expr, ast.Attribute)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return False
    return expr.id in scope.aliases or (through_attribute and expr.id in scope.selves)


def _holds_state(expr: ast.expr, scope: _Scope) -> bool:
    """Tell whether `expr` is `self` itself or state it holds."""
    is_self = isinstance(expr, ast.Name) and expr.id in scope.selves
    return is_self or _on_state(expr, scope)


def _bindings(method: _Method) -> Iterator[tuple[str, ast.expr]]:
    for node in ast.walk(method):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
        elif (
            isinstance(node, ast.AnnAssign | ast.NamedExpr)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            yield node.target.id, node.value


def _scope(method: _Method) -> _Scope:
    """Collect the locals bound to `self` or its state, through chains of aliases.

    A slice copies, so a name bound to one is no alias.
    """
    bindings = [
        (name, value)
        for name, value in _bindings(method)
        if not (isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Slice))
    ]
    scope = _Scope(frozenset({"self"}), frozenset())
    while True:
        selves = {
            name
            for name, value in bindings
            if isinstance(value, ast.Name) and value.id in scope.selves
        }
        aliases = {name for name, value in bindings if _on_state(value, scope)}
        grown = _Scope(scope.selves | selves, scope.aliases | aliases)
        if grown == scope:
            return scope
        scope = grown


def _targets(node: ast.AST) -> Iterator[ast.expr]:
    roots: list[ast.expr] = []
    if isinstance(node, ast.Assign | ast.Delete):
        roots = list(node.targets)
    elif isinstance(node, ast.AugAssign) or (
        isinstance(node, ast.AnnAssign) and node.value is not None
    ):
        roots = [node.target]
    while roots:
        target = roots.pop()
        if isinstance(target, ast.Tuple | ast.List):
            roots.extend(target.elts)
        elif isinstance(target, ast.Starred):
            roots.append(target.value)
        else:
            yield target


def _is_super(expr: ast.expr) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "super"
    )


def _writes_through_call(node: ast.Call, scope: _Scope) -> bool:
    func = node.func
    first = node.args[0] if node.args else None
    first_holds = first is not None and _holds_state(first, scope)
    if isinstance(func, ast.Name):
        return func.id in _ATTRIBUTE_WRITERS and first_holds
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _MUTATORS:
        return _on_state(func.value, scope)
    if func.attr in _DUNDER_WRITERS:
        receiver = func.value
        return _is_super(receiver) or _holds_state(receiver, scope) or first_holds
    return False


def _mutates(node: ast.AST, scope: _Scope) -> bool:
    if any(
        isinstance(target, ast.Attribute | ast.Subscript) and _on_state(target, scope)
        for target in _targets(node)
    ):
        return True
    return isinstance(node, ast.Call) and _writes_through_call(node, scope)


def _reached(method: _Method, scope: _Scope) -> tuple[set[str], set[str]]:
    """Return the attribute names `method` reads through `self` and through `super()`."""
    via_self: set[str] = set()
    via_super: set[str] = set()
    for node in ast.walk(method):
        if not isinstance(node, ast.Attribute):
            continue
        receiver = node.value
        if isinstance(receiver, ast.Name) and receiver.id in scope.selves:
            via_self.add(node.attr)
        elif _is_super(receiver):
            via_super.add(node.attr)
    return via_self, via_super


def _render_path(mro: Sequence[_Class]) -> Iterator[tuple[_Class, _Method, _Scope]]:
    """Yield `render` and every method it reaches through `self` or `super()`."""
    entry = _lookup(mro, "render")
    pending = [] if entry is None else [entry]
    seen: set[int] = set()
    while pending:
        owner, method = pending.pop(0)
        if id(method) in seen:
            continue
        seen.add(id(method))
        scope = _scope(method)
        yield owner, method, scope
        via_self, via_super = _reached(method, scope)
        above = mro[mro.index(owner) + 1 :]
        reached = [_lookup(mro, name) for name in sorted(via_self)]
        reached += [_lookup(above, name) for name in sorted(via_super)]
        pending.extend(found for found in reached if found is not None)


def _violations(sources: Mapping[str, str]) -> list[str]:
    """List `module:line class.method` for every write to node state while rendering."""
    classes, aliases = _classes(sources)
    violations: dict[str, None] = {}
    for name in _rendering(classes, aliases):
        mro = list(_mro(name, classes, aliases, set()))
        for owner, method, scope in _render_path(mro):
            lines = sorted(
                {node.lineno for node in ast.walk(method) if _mutates(node, scope)}
            )
            violations.update(
                dict.fromkeys(
                    f"{owner.module}:{line} {owner.node.name}.{method.name}"
                    for line in lines
                )
            )
    return list(violations)


def _package_sources() -> dict[str, str]:
    spec = importlib.util.find_spec("next")
    assert spec is not None
    assert spec.origin is not None
    package = Path(spec.origin).parent
    return {
        path.relative_to(package.parent).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(package.rglob("*.py"))
    }


_PACKAGE_SOURCES = _package_sources()


class TestTemplateNodeState:
    """No template node of the framework writes its own state while it renders.

    Compiled templates are cached and rendered by many threads at once.
    """

    def test_no_render_writes_node_state(self) -> None:
        violations = _violations(_PACKAGE_SOURCES)
        assert not violations, (
            "Template nodes write their own state while rendering, keep per-render "
            "state in context.render_context:\n" + "\n".join(violations)
        )

    @pytest.mark.parametrize(
        "name",
        [
            "next.templatetags.components.ComponentNode",
            "next.templatetags.scripts.ConsentedNode",
            "next.forms.nodes.FormNode",
            "next.partial.zone.ZoneNode",
        ],
    )
    def test_the_scan_reaches_the_node(self, name: str) -> None:
        assert name in _rendering_classes(_PACKAGE_SOURCES)

    def test_the_scan_skips_the_json_ld_node(self) -> None:
        assert "next.pages.metadata.ld.ListItem" not in _rendering_classes(
            _PACKAGE_SOURCES
        )


class TestNodeStateScanner:
    """The scanner flags every kind of write to a `self` attribute it promises."""

    @pytest.mark.parametrize("case", NODE_STATE_VIOLATION_CASES, ids=lambda c: c.id)
    def test_flags_the_write(self, case: NodeStateCase) -> None:
        assert _violations(dict(case.sources)) == list(case.expected)

    @pytest.mark.parametrize("case", NODE_STATE_CLEAN_CASES, ids=lambda c: c.id)
    def test_passes_the_clean_class(self, case: NodeStateCase) -> None:
        assert _violations(dict(case.sources)) == list(case.expected)
