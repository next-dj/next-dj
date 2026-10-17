"""The boundary rules one source file breaks on its own, read from its syntax."""

import ast
from collections.abc import Iterator
from typing import Final

from .rules import (
    AGGREGATOR,
    FOREIGN_FILE,
    FOREIGN_PERMISSION,
    PACKAGE_MODEL,
    STRAY_SEND,
    TREE_WRITE,
    UNSCOPED_QUERY,
    Finding,
)
from .sources import (
    Project,
    SourceFile,
    absolute_module,
    argument,
    assign_targets,
    assigns,
    call_name,
    dotted,
    last_name,
    string,
    within,
)


FOREIGN_FILES: Final[dict[str, str]] = {
    "providers.py": "deps.py",
    "receivers.py": "signals.py",
    "policy.py": "permissions.py",
    "access.py": "permissions.py",
    "facades.py": "services.py",
    "selectors.py": "services.py",
    "queries.py": "services.py",
}

_SENDS: Final = frozenset({"send", "send_robust"})
_SCOPES: Final = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_FUNCTIONS: Final = (ast.FunctionDef, ast.AsyncFunctionDef)
_SUPER: Final = "super"
_FORM: Final = "form"
_FORM_PREFIX: Final = "form_"
_FORM_SUFFIX: Final = "_form"
_FORM_CLASS: Final = "Form"
_SELF: Final = "self"
_INIT: Final = "__init__.py"
_ALL: Final = "__all__"


def _finding(
    project: Project, source: SourceFile, code: str, line: int, message: str
) -> Finding:
    """Return a finding of `code` at `line` of a file."""
    return Finding(project.relative(source.path), line, code, source.app.label, message)


def _visible_to_receivers(syntax: ast.Module) -> set[int]:
    """Return the ids of the nodes a `.visible_to(` call is made on."""
    return {
        id(node.func.value)
        for node in ast.walk(syntax)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "visible_to"
    }


def _unscoped_queries(
    project: Project, source: SourceFile, syntax: ast.Module
) -> Iterator[Finding]:
    """Yield each `.objects` of a page tree file not followed by `.visible_to(`."""
    scoped = _visible_to_receivers(syntax)
    for node in ast.walk(syntax):
        if not isinstance(node, ast.Attribute) or node.attr != "objects":
            continue
        if id(node) in scoped:
            continue
        message = f"A page tree reads {ast.unparse(node)} without .visible_to(...)."
        yield _finding(project, source, UNSCOPED_QUERY, node.lineno, message)


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Yield the nodes of one scope, leaving nested functions and classes out."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _subject(node: ast.expr) -> str | None:
    """Return the name a `.save()` is called on, `super` for `super().save()`."""
    return call_name(node) if isinstance(node, ast.Call) else last_name(node)


def _formlike(name: str | None) -> bool:
    """Return whether a name names a form, as `form`, `*_form`, `form_*` or `*Form`.

    A name that only holds the letters, like `platform`, is no form.
    """
    if name is None:
        return False
    return (
        name.lower() == _FORM
        or name.lower().endswith(_FORM_SUFFIX)
        or name.lower().startswith(_FORM_PREFIX)
        or name.endswith(_FORM_CLASS)
    )


def _saved_objects(nodes: list[ast.AST]) -> Iterator[tuple[ast.expr, int]]:
    """Yield what each `.save()` call of a scope is made on, with its line."""
    for node in nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "save"
        ):
            yield node.func.value, node.lineno


def _assigned_attributes(nodes: list[ast.AST]) -> Iterator[ast.Attribute]:
    """Yield every attribute a scope assigns to."""
    for node in nodes:
        yield from (
            target
            for target in assign_targets(node)
            if isinstance(target, ast.Attribute)
        )


def _form_class(node: ast.ClassDef) -> bool:
    """Return whether a class or one of its written bases is named like a form."""
    names = [node.name, *(last_name(base) for base in node.bases)]
    return any(name is not None and name.endswith(_FORM_CLASS) for name in names)


def _form_methods(syntax: ast.Module) -> set[int]:
    """Return the ids of the methods written in the body of a form class."""
    return {
        id(item)
        for node in ast.walk(syntax)
        if isinstance(node, ast.ClassDef) and _form_class(node)
        for item in node.body
        if isinstance(item, _FUNCTIONS)
    }


def _scope_writes(
    project: Project, source: SourceFile, scope: ast.AST, *, form: bool = False
) -> Iterator[Finding]:
    """Yield the writes one scope of a page tree file makes past a form.

    A method of a form saves the form itself through `self.save()`.
    """
    nodes = list(_scope_nodes(scope))
    saved: set[str] = set()
    for target, line in _saved_objects(nodes):
        if form and isinstance(target, ast.Name) and target.id == _SELF:
            continue
        if isinstance(target, ast.Name):
            saved.add(target.id)
        subject = _subject(target)
        if subject == _SUPER or _formlike(subject):
            continue
        message = f"A page tree calls {ast.unparse(target)}.save() past a form."
        yield _finding(project, source, TREE_WRITE, line, message)
    for attribute in _assigned_attributes(nodes):
        owner = attribute.value
        if isinstance(owner, ast.Name) and owner.id in saved:
            message = (
                f"A page tree sets {ast.unparse(attribute)} on an object it saves "
                "in the same function."
            )
            yield _finding(project, source, TREE_WRITE, attribute.lineno, message)


def _tree_writes(
    project: Project, source: SourceFile, syntax: ast.Module
) -> Iterator[Finding]:
    """Yield the writes of every scope of a page tree file."""
    yield from _scope_writes(project, source, syntax)
    methods = _form_methods(syntax)
    for node in ast.walk(syntax):
        if isinstance(node, _FUNCTIONS):
            form = id(node) in methods
            yield from _scope_writes(project, source, node, form=form)


def project_signals(project: Project) -> frozenset[str]:
    """Return the dotted path of each `Signal()` a `signals.py` of the project binds."""
    found: set[str] = set()
    for source in project.files:
        if not within(source.app, source.path, "signals"):
            continue
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        for node in syntax.body:
            value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
            if not isinstance(value, ast.Call) or call_name(value) != "Signal":
                continue
            found.update(
                f"{source.module}.{target.id}"
                for target in assign_targets(node)
                if isinstance(target, ast.Name)
            )
    return frozenset(found)


def _stray_sends(
    project: Project, source: SourceFile, syntax: ast.Module, signals: frozenset[str]
) -> Iterator[Finding]:
    """Yield each send of a project signal made outside the services module."""
    if within(source.app, source.path, "services"):
        return
    names = project.names(source, syntax)
    for node in ast.walk(syntax):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _SENDS:
            continue
        path = dotted(node.func.value, names, source.module)
        if path in signals:
            message = (
                f"{source.app.label} sends the project signal {path} outside "
                "services.py."
            )
            yield _finding(project, source, STRAY_SEND, node.lineno, message)


def _model_bases(node: ast.ClassDef) -> bool:
    """Return whether a class derives from a class named `Model`."""
    return any(last_name(base) == "Model" for base in node.bases)


def _package_models(
    project: Project, source: SourceFile, syntax: ast.Module
) -> Iterator[Finding]:
    """Yield each model class the `__init__.py` at the root of an app defines."""
    if source.path != source.app.path / _INIT:
        return
    for node in ast.walk(syntax):
        if isinstance(node, ast.ClassDef) and _model_bases(node):
            message = (
                f"{source.app.label} defines the model {node.name} in __init__.py."
            )
            yield _finding(project, source, PACKAGE_MODEL, node.lineno, message)


def _exported(syntax: ast.Module) -> list[tuple[str, int]]:
    """Return each name a literal module-level `__all__` lists, with its line."""
    found: list[tuple[str, int]] = []
    for node in syntax.body:
        if not assigns(node, _ALL):
            continue
        value = (
            node.value
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign)
            else None
        )
        if isinstance(value, ast.List | ast.Tuple):
            found.extend(
                (name, node.lineno)
                for item in value.elts
                if (name := string(item)) is not None
            )
    return found


def _aggregators(
    project: Project, source: SourceFile, syntax: ast.Module
) -> Iterator[Finding]:
    """Yield each name of another app a module re-exports, by `__all__` or by `*`."""
    label = source.app.label
    for node in ast.walk(syntax):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not any(alias.name == "*" for alias in node.names):
            continue
        module = absolute_module(node, source)
        owner = None if module is None else project.app_of_module(module)
        if owner is not None and owner != source.app:
            message = f"{label} re-exports everything of {module} with import *."
            yield _finding(project, source, AGGREGATOR, node.lineno, message)
    names = project.names(source, syntax)
    for name, line in _exported(syntax):
        path = names.get(name)
        owner = None if path is None else project.app_of_module(path)
        if owner is not None and owner != source.app:
            message = f"{label} lists {name} of {owner.label} in __all__."
            yield _finding(project, source, AGGREGATOR, line, message)


def _foreign_permissions(
    project: Project, source: SourceFile, syntax: ast.Module
) -> Iterator[Finding]:
    """Yield each `add_perm` of a permission whose label names another app."""
    for node in ast.walk(syntax):
        if not isinstance(node, ast.Call) or call_name(node) != "add_perm":
            continue
        perm = string(argument(node, "name", 0))
        owner = None if perm is None else project.foreign_label(perm, source.app)
        if owner is None:
            continue
        message = (
            f"{source.app.label} registers the permission {perm!r} of the app "
            f"{owner.label} with add_perm."
        )
        yield _finding(project, source, FOREIGN_PERMISSION, node.lineno, message)


def _foreign_files(project: Project) -> Iterator[Finding]:
    """Yield each file at the root of an app named for a role next.dj does not read."""
    for app in project.apps:
        for name, instead in sorted(FOREIGN_FILES.items()):
            path = app.path / name
            if not path.is_file():
                continue
            yield Finding(
                project.relative(path),
                0,
                FOREIGN_FILE,
                app.label,
                f"{app.label} has {name} at its root, a module next.dj does not "
                f"read. Its code belongs in {instead}.",
            )


def source_findings(project: Project) -> Iterator[Finding]:
    """Yield what every parsed file breaks on its own, and the stray root files."""
    signals = project_signals(project)
    for source in project.files:
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        if source.tree:
            yield from _unscoped_queries(project, source, syntax)
            yield from _tree_writes(project, source, syntax)
        yield from _stray_sends(project, source, syntax, signals)
        yield from _package_models(project, source, syntax)
        yield from _aggregators(project, source, syntax)
        yield from _foreign_permissions(project, source, syntax)
    yield from _foreign_files(project)


__all__ = ["FOREIGN_FILES", "project_signals", "source_findings"]
