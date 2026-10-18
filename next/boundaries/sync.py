"""`next boundaries sync`, the `depends_on` each app needs, written to its `AppConfig`.

It only adds names, and a value it cannot read as a literal is left to the hand.
"""

import ast
import difflib
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from django.apps import AppConfig, apps as django_apps

from .declared import DEPENDS_ON
from .errors import SyncError, UnknownAppError
from .graph import App, Graph
from .manager import Scan
from .sources import Project, assigns, class_file, class_node


_WIDTH: Final = 88
_STEP: Final = "    "
_ANCHORS: Final = frozenset({"name", "label", "verbose_name", "default_auto_field"})


@dataclass(frozen=True, slots=True)
class Change:
    """One file `sync` rewrites, its text before and after."""

    path: Path
    before: str
    after: str

    def diff(self, project: Project) -> str:
        """Return the unified diff of the change, its path relative to `BASE_DIR`."""
        name = project.relative(self.path)
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )


@dataclass(frozen=True, slots=True)
class _Splice:
    """Lines `start` to `end` of a file, zero based and open, and what replaces them."""

    start: int
    end: int
    lines: tuple[str, ...]


def _needed(graph: Graph, app: App) -> tuple[str, ...]:
    """Return the name of every app of the project this one has an edge to, sorted."""
    names = graph.names
    return tuple(sorted({names[edge.target] for edge in graph.outgoing(app.label)}))


def _assignment(node: ast.ClassDef) -> tuple[ast.stmt, ast.expr | None] | None:
    """Return the last statement of a class body that assigns `depends_on`.

    The value comes along only for a plain assignment to the one name.
    """
    found = None
    for statement in node.body:
        if not assigns(statement, DEPENDS_ON):
            continue
        found = statement, _bound(statement)
    return found


def _bound(statement: ast.stmt) -> ast.expr | None:
    """Return the value a plain assignment to one name binds, `None` for any other."""
    if isinstance(statement, ast.AnnAssign):
        return statement.value
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        return statement.value
    return None


def _literal(value: ast.expr | None) -> ast.Tuple | None:
    """Return the tuple a `depends_on` assignment binds when it holds only strings."""
    if not isinstance(value, ast.Tuple) or not all(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in value.elts
    ):
        return None
    return value


def _rendered(
    prefix: str, names: tuple[str, ...], suffix: str, indent: str
) -> tuple[str, ...]:
    """Return the lines of a tuple of names, as `ruff format` would lay it out."""
    items = [f'"{name}"' for name in names]
    flat = f"({items[0]},)" if len(items) == 1 else f"({', '.join(items)})"
    line = f"{prefix}{flat}{suffix}"
    if len(line.rstrip("\r\n")) <= _WIDTH or not items:
        return (line,)
    return (
        f"{prefix}(\n",
        *(f"{indent}{_STEP}{item},\n" for item in items),
        f"{indent}){suffix}",
    )


def _indent(lines: list[str], statement: ast.stmt) -> str:
    """Return the whitespace a statement of a class body is indented by."""
    line = lines[statement.lineno - 1]
    return line[: len(line) - len(line.lstrip())]


def _cut(line: str, offset: int) -> tuple[str, str]:
    """Split a line at a byte offset of its UTF-8 text, as `ast` counts columns."""
    raw = line.encode("utf-8")
    return raw[:offset].decode("utf-8"), raw[offset:].decode("utf-8")


def _rewrite(
    lines: list[str], value: ast.Tuple, names: tuple[str, ...], indent: str
) -> _Splice:
    """Return the splice that puts `names` in place of a literal tuple."""
    first, last = value.lineno - 1, (value.end_lineno or value.lineno) - 1
    prefix, _rest = _cut(lines[first], value.col_offset)
    _head, suffix = _cut(lines[last], value.end_col_offset or 0)
    return _Splice(first, last + 1, _rendered(prefix, names, suffix, indent))


def _placeholder(statement: ast.stmt) -> bool:
    """Return whether a statement is a bare `pass` or `...` holding a body open."""
    return isinstance(statement, ast.Pass) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


def _insert(
    file: str, lines: list[str], node: ast.ClassDef, names: tuple[str, ...]
) -> _Splice:
    """Return the splice that adds `depends_on` to a class that has none.

    A body that is only `pass` or `...` gives its line to the new attribute.
    """
    anchors = [
        statement
        for statement in node.body
        if any(assigns(statement, name) for name in _ANCHORS)
    ]
    first = node.body[0]
    documented = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    after = anchors[-1] if anchors else (first if documented else None)
    near = after or first
    if near.lineno == node.lineno:
        reason = f"The class {node.name} keeps its body on the line of its name."
        raise SyncError(file, node.lineno, reason)
    indent = _indent(lines, near)
    rendered = _rendered(f"{indent}{DEPENDS_ON} = ", names, "\n", indent)
    if len(node.body) == 1 and _placeholder(first):
        return _Splice(first.lineno - 1, first.end_lineno or first.lineno, rendered)
    at = (after.end_lineno or after.lineno) if after is not None else first.lineno - 1
    if documented and not anchors:
        rendered = ("\n", *rendered)
    if not lines[at - 1].endswith("\n"):
        rendered = ("\n", *rendered)
    return _Splice(at, at, rendered)


@dataclass(frozen=True, slots=True)
class Plan:
    """The files `sync` rewrites and the apps it leaves without an AppConfig of theirs.

    Such an app runs on a class outside the project, so sync has no file to write.
    """

    changes: tuple[Change, ...]
    skipped: tuple[str, ...]


_OWN_CONFIG: Final = "Give it an apps.py with an AppConfig subclass, then sync again."


def _own_file(project: Project, config: AppConfig) -> Path | None:
    """Return the file of the class of `config` when an app of the project holds it."""
    path = class_file(type(config))
    if path is None or project.app_of(path) is None:
        return None
    return path


def _splice(
    project: Project, graph: Graph, app: App, path: Path, lines: dict[Path, list[str]]
) -> tuple[Path, _Splice] | None:
    """Return where and how `sync` adds the missing names of one app, if any."""
    config = django_apps.get_app_config(app.label)
    syntax = project.syntax(path)
    shown = project.relative(path)
    if syntax is None:
        raise SyncError(shown, 0, f"The AppConfig of {app.label} cannot be read.")
    name = type(config).__name__
    node = class_node(syntax, name)
    if node is None:
        reason = f"The module defines no class {name} at its top level."
        raise SyncError(shown, 0, reason)
    text = lines.setdefault(
        path, path.read_text(encoding="utf-8").splitlines(keepends=True)
    )
    needed = _needed(graph, app)
    assignment = _assignment(node)
    if assignment is None:
        if getattr(config, DEPENDS_ON, None) is not None:
            reason = f"The class {name} inherits depends_on."
            raise SyncError(shown, node.lineno, reason)
        return path, _insert(shown, text, node, needed)
    statement, bound = assignment
    value = _literal(bound)
    if value is None:
        reason = "depends_on is no literal tuple of strings."
        raise SyncError(shown, statement.lineno, reason)
    current = tuple(
        item.value
        for item in value.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    )
    missing = tuple(item for item in needed if item not in current)
    if not missing:
        return None
    indent = _indent(text, statement)
    return path, _rewrite(text, value, (*current, *missing), indent)


def plan(scan: Scan, key: str | None = None) -> Plan:
    """Return the files `sync` rewrites, for every app or the one `key` names.

    Every text is built before any is written, so one refusal leaves all files alone.
    """
    graph = scan.graph
    if key is None:
        apps = graph.apps
    else:
        found = graph.lookup(key)
        if found is None:
            raise UnknownAppError(key, tuple(item.label for item in graph.apps))
        apps = (found,)
    project = scan.project
    if project is None:
        return Plan((), ())
    lines: dict[Path, list[str]] = {}
    splices: dict[Path, list[_Splice]] = defaultdict(list)
    skipped: list[str] = []
    for app in apps:
        path = _own_file(project, django_apps.get_app_config(app.label))
        if path is None:
            if key is not None:
                reason = (
                    f"{app.label} has no AppConfig class of its own in the project."
                )
                raise SyncError(project.relative(app.path), 0, reason, _OWN_CONFIG)
            skipped.append(app.label)
            continue
        found_splice = _splice(project, graph, app, path, lines)
        if found_splice is not None:
            target, splice = found_splice
            splices[target].append(splice)
    changes: list[Change] = []
    for target, edits in sorted(splices.items()):
        before = "".join(lines[target])
        after = list(lines[target])
        for edit in sorted(edits, key=lambda item: item.start, reverse=True):
            after[edit.start : edit.end] = edit.lines
        changes.append(Change(target, before, "".join(after)))
    return Plan(tuple(changes), tuple(skipped))


def _stage(change: Change) -> Path:
    """Write the new text of a change to a temporary file beside it, its mode kept."""
    handle, name = tempfile.mkstemp(
        dir=change.path.parent, prefix=f".{change.path.name}.", suffix=".tmp"
    )
    temp = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(change.after)
        if change.path.exists():
            shutil.copymode(change.path, temp)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    return temp


def _discard(staged: list[tuple[Path, Path]]) -> None:
    """Remove the staged files that have not replaced their file."""
    for temp, _path in staged:
        temp.unlink(missing_ok=True)


def apply(changes: tuple[Change, ...], project: Project) -> None:
    """Write every change, each staged beside its file before any file is replaced.

    A failure while staging removes the staged files and leaves every file as it was.
    """
    staged: list[tuple[Path, Path]] = []
    for change in changes:
        try:
            temp = _stage(change)
        except OSError as exc:
            _discard(staged)
            raise SyncError(project.relative(change.path), 0, str(exc)) from exc
        staged.append((temp, change.path))
    for index, (temp, path) in enumerate(staged):
        try:
            temp.replace(path)
        except OSError as exc:
            _discard(staged[index:])
            written = f" Files already written before it: {index}." if index else ""
            reason = f"{exc}.{written}"
            raise SyncError(project.relative(path), 0, reason) from exc


__all__ = ["Change", "Plan", "apply", "plan"]
