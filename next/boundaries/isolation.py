"""What the services of an isolated app return, read from their annotations alone.

The check reads syntax and the modules already loaded, and it imports no project code.
"""

import ast
import builtins
import enum
import importlib.util
import inspect
import sys
import typing
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final, Literal

from django.apps import apps as django_apps
from django.db import models
from django.db.models.manager import BaseManager
from django.db.models.query import QuerySet, RawQuerySet

from .graph import App
from .rules import ISOLATED_SERVICE, PRIVATE_IMPORT, UNANNOTATED_SERVICE, Finding
from .sources import Project, SourceFile, absolute_module, imported_names, within


type Kind = Literal[
    "model", "queryset", "any", "class", "unknown", "enum", "data", "fine"
]

_QUERYSETS: Final = frozenset(
    {
        "django.db.models.QuerySet",
        "django.db.models.query.QuerySet",
        "django.db.models.Manager",
        "django.db.models.manager.Manager",
        "django.db.models.manager.BaseManager",
        "django.db.models.query.RawQuerySet",
    }
)
_ANYTHING: Final = frozenset({"typing.Any", "typing_extensions.Any", "builtins.object"})

_ANNOTATED: Final = frozenset({"typing.Annotated", "typing_extensions.Annotated"})
_LITERAL: Final = frozenset({"typing.Literal", "typing_extensions.Literal"})
_DATACLASS: Final = "dataclasses.dataclass"
_REQUIRED_FLAGS: Final = frozenset({"frozen", "slots"})
_MISSING: Final = object()
_FUNCTIONS: Final = (ast.FunctionDef, ast.AsyncFunctionDef)
_FROM_QUERYSET: Final = "from_queryset"
_CONTAINERS: Final = (
    "Iterable",
    "Iterator",
    "Sequence",
    "MutableSequence",
    "Collection",
    "Container",
    "Mapping",
    "MutableMapping",
    "Set",
    "MutableSet",
    "Generator",
    "AsyncIterable",
    "AsyncIterator",
    "AsyncGenerator",
    "Awaitable",
    "Coroutine",
)
_BARE: Final = frozenset(
    {
        *(f"builtins.{name}" for name in ("list", "dict", "tuple", "set", "frozenset")),
        "builtins.type",
        *(f"collections.abc.{name}" for name in _CONTAINERS),
        *(f"typing.{name}" for name in _CONTAINERS),
        *(
            f"typing.{name}"
            for name in ("List", "Dict", "Tuple", "Set", "FrozenSet", "Type")
        ),
        "django.core.paginator.Page",
    }
)
_LEAKS: Final = {"model": "a model", "queryset": "a queryset or manager"}


@dataclass(frozen=True, slots=True)
class _Scope:
    """One parsed module of the project, the names it imports and those it defines."""

    module: str
    names: dict[str, str]
    defined: dict[str, ast.ClassDef | ast.expr]


@dataclass(frozen=True, slots=True)
class _Defined:
    """The class of the project, or the value its alias binds, defining `path`."""

    scope: _Scope
    node: ast.ClassDef | ast.expr
    path: str


def _definitions(syntax: ast.Module) -> dict[str, ast.ClassDef | ast.expr]:
    """Map each name the body of a module defines to its class or the value it binds."""
    defined: dict[str, ast.ClassDef | ast.expr] = {}
    for node in syntax.body:
        if isinstance(node, ast.ClassDef):
            defined[node.name] = node
        elif isinstance(node, ast.TypeAlias):
            defined[node.name.id] = node.value
        elif isinstance(node, ast.Assign):
            defined.update(
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            defined[node.target.id] = node.value
    return defined


def _top_imports(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the import statements at the top of a module, inside `if` and `try` too."""
    for node in body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node
        elif isinstance(node, ast.If):
            yield from _top_imports([*node.body, *node.orelse])
        elif isinstance(node, ast.Try):
            handled = [item for handler in node.handlers for item in handler.body]
            yield from _top_imports(
                [*node.body, *handled, *node.orelse, *node.finalbody]
            )


def _top_names(source: SourceFile, syntax: ast.Module) -> dict[str, str]:
    """Map each name the module level imports bind to its dotted path.

    An import inside a function binds a local name, so it never shadows the module.
    """
    imports = ast.Module(body=list(_top_imports(syntax.body)), type_ignores=[])
    return imported_names(imports, source)


def _loaded(path: str) -> object:
    """Return what the dotted `path` names among the loaded modules, never importing.

    What it cannot reach answers `None`, and only an unknown top module `_MISSING`.
    """
    parts = path.split(".")
    for size in range(len(parts), 0, -1):
        module = sys.modules.get(".".join(parts[:size]))
        if module is not None:
            break
    else:
        return None if importlib.util.find_spec(parts[0]) is not None else _MISSING
    found: object = module
    for part in parts[size:]:
        found = inspect.getattr_static(found, part, None)
    return found


def _manager_of(node: ast.expr) -> ast.expr | None:
    """Return the manager class a `from_queryset` call extends, `None` for any other."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == _FROM_QUERYSET
    ):
        return node.func.value
    return None


def _kind_of(found: object) -> Kind:
    """Return what a loaded object stands for as a return type."""
    if found is typing.Any or found is object:
        return "any"
    if not isinstance(found, type):
        return "fine"
    if issubclass(found, models.Model):
        return "model"
    if issubclass(found, QuerySet | BaseManager | RawQuerySet):
        return "queryset"
    if issubclass(found, enum.Enum):
        return "enum"
    return "fine"


def _describe(kind: Kind, path: str, written: str) -> str | None:
    """Return how a finding spells a return type of `kind`, `None` for a fine one."""
    if kind == "model":
        return f"the model {path}"
    if kind == "queryset":
        return f"the queryset or manager {path}"
    if kind == "any":
        return path
    if kind == "class":
        return f"the class {path}, which is no frozen slots dataclass or enum"
    if kind == "unknown":
        return f"{written}, and the check cannot tell what {written} is"
    return None


@dataclass(slots=True)
class _Reader:
    """Resolves the names of annotations through the project and its re-exports."""

    project: Project
    models: frozenset[str]
    _scopes: dict[str, _Scope] = field(default_factory=dict)

    def scope(self, module: str) -> _Scope | None:
        """Return the module `module` of the project, `None` when it does not parse."""
        source = self.project.modules[module]
        syntax = self.project.syntax(source.path)
        return None if syntax is None else self.parsed(source, syntax)

    def parsed(self, source: SourceFile, syntax: ast.Module) -> _Scope:
        """Return the names a parsed file imports and defines, read once per file."""
        found = self._scopes.get(source.module)
        if found is None:
            names = _top_names(source, syntax)
            found = _Scope(source.module, names, _definitions(syntax))
            self._scopes[source.module] = found
        return found

    def path_of(self, scope: _Scope, node: ast.expr) -> str | None:
        """Return the dotted path a name or attribute of a module stands for.

        A manager built by `from_queryset` stands for the manager class it is called on.
        """
        manager = _manager_of(node)
        if manager is not None:
            return self.path_of(scope, manager)
        if isinstance(node, ast.Attribute):
            head = self.path_of(scope, node.value)
            return None if head is None else f"{head}.{node.attr}"
        if not isinstance(node, ast.Name):
            return None
        if node.id in scope.defined:
            return f"{scope.module}.{node.id}"
        if node.id in scope.names:
            return scope.names[node.id]
        return f"builtins.{node.id}" if hasattr(builtins, node.id) else None

    def _split(self, path: str) -> tuple[str, str] | None:
        """Return the longest project module `path` lies in and the rest of it."""
        parts = path.split(".")
        for size in range(len(parts), 0, -1):
            module = ".".join(parts[:size])
            if module in self.project.modules:
                return module, ".".join(parts[size:])
        return None

    def locate(self, path: str, seen: frozenset[str]) -> _Defined | str | None:
        """Follow re-exports to the project statement defining `path`.

        An outside path comes back as it is, and an unknown or circular one as `None`.
        """
        if path in seen:
            return None
        split = self._split(path)
        if split is None:
            return path
        module, rest = split
        scope = self.scope(module)
        head, _dot, tail = rest.partition(".")
        if scope is None or not head:
            return None
        defined = scope.defined.get(head)
        if defined is not None:
            return None if tail else _Defined(scope, defined, f"{module}.{head}")
        target = scope.names.get(head)
        if target is None:
            return None
        return self.locate(f"{target}.{tail}" if tail else target, seen | {path})

    def outside(self, path: str) -> Kind:
        """Return what a path outside the project stands for."""
        if path in _QUERYSETS:
            return "queryset"
        if path in _ANYTHING:
            return "any"
        if path in self.models:
            return "model"
        found = _loaded(path)
        return "unknown" if found is _MISSING else _kind_of(found)

    def kind(self, scope: _Scope, node: ast.expr, seen: frozenset[str]) -> Kind:
        """Return what a name or attribute names, an alias followed to what it binds."""
        path = self.path_of(scope, node)
        return "unknown" if path is None else self.path_kind(path, seen)

    def path_kind(self, path: str, seen: frozenset[str]) -> Kind:
        """Return what the dotted `path` names, through re-exports and aliases."""
        located = self.locate(path, seen)
        if located is None:
            return "unknown"
        if isinstance(located, str):
            return self.outside(located)
        if isinstance(located.node, ast.ClassDef):
            return self.class_kind(located.scope, located.node, located.path, seen)
        return self.kind(located.scope, located.node, seen | {located.path})

    def class_kind(
        self, scope: _Scope, node: ast.ClassDef, path: str, seen: frozenset[str]
    ) -> Kind:
        """Return what a class of the project stands for, by its bases and decorators.

        The registry knows the concrete models, and the bases tell an abstract one.
        """
        if path in self.models:
            return "model"
        inner = seen | {path}
        bases = {
            self.kind(
                scope, base.value if isinstance(base, ast.Subscript) else base, inner
            )
            for base in node.bases
        }
        if "model" in bases:
            return "model"
        if "queryset" in bases:
            return "queryset"
        if "enum" in bases:
            return "enum"
        return "data" if self.frozen_slots(scope, node) else "class"

    def frozen_slots(self, scope: _Scope, statement: ast.ClassDef) -> bool:
        """Return whether a class is decorated `@dataclass(frozen=True, slots=True)`."""
        for decorator in statement.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if self.path_of(scope, decorator.func) != _DATACLASS:
                continue
            flags = {
                item.arg
                for item in decorator.keywords
                if isinstance(item.value, ast.Constant) and item.value.value is True
            }
            if flags >= _REQUIRED_FLAGS:
                return True
        return False

    def reason(self, scope: _Scope, node: ast.expr, seen: frozenset[str]) -> str | None:
        """Return the first part of an annotation an isolated service may not return.

        A string is read as the annotation it spells, and `Literal` holds no names.
        """
        if isinstance(node, ast.Constant):
            return self._string(scope, node.value, seen)
        if isinstance(node, ast.BinOp):
            return self._first(scope, [node.left, node.right], seen)
        if isinstance(node, ast.Tuple | ast.List):
            return self._first(scope, node.elts, seen)
        if isinstance(node, ast.Subscript):
            return self._subscript(scope, node, seen)
        return self._named(scope, node, seen)

    def _named(
        self, scope: _Scope, node: ast.expr, seen: frozenset[str], *, bare: bool = True
    ) -> str | None:
        """Return the reason of a name or attribute, an alias followed to its value.

        A bare container hides what it holds, and a class already being read adds none.
        """
        written = ast.unparse(node)
        path = self.path_of(scope, node)
        if path is not None and path in seen:
            return None
        located = None if path is None else self.locate(path, seen)
        if located is None:
            return _describe("unknown", written, written)
        if isinstance(located, str):
            return self._outside_reason(located, written, bare=bare)
        if isinstance(located.node, ast.ClassDef):
            return self._class_reason(
                located.scope, located.node, located.path, seen, written
            )
        return self.reason(located.scope, located.node, seen | {located.path})

    def _outside_reason(self, path: str, written: str, *, bare: bool) -> str | None:
        """Return the reason of a path outside the project, bare containers included."""
        if bare and path in _BARE:
            return (
                f"{written} without its parameters, and the check cannot tell what it "
                "holds"
            )
        return _describe(self.outside(path), path, written)

    def _class_reason(
        self,
        scope: _Scope,
        node: ast.ClassDef,
        path: str,
        seen: frozenset[str],
        written: str,
    ) -> str | None:
        """Return the reason of a project class, a dataclass read field by field."""
        kind = self.class_kind(scope, node, path, seen)
        if kind != "data":
            return _describe(kind, path, written)
        inner = seen | {path}
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(
                statement.target, ast.Name
            ):
                continue
            found = self.reason(scope, statement.annotation, inner)
            if found is not None:
                return (
                    f"the dataclass {path}, whose field {statement.target.id} holds "
                    f"{found}"
                )
        return None

    def _string(self, scope: _Scope, value: object, seen: frozenset[str]) -> str | None:
        """Return the reason of a constant, a string read as the annotation it holds."""
        if not isinstance(value, str):
            return None
        try:
            parsed = ast.parse(value, mode="eval")
        except SyntaxError:
            return _describe("unknown", value, value)
        return self.reason(scope, parsed.body, seen)

    def _subscript(
        self, scope: _Scope, node: ast.Subscript, seen: frozenset[str]
    ) -> str | None:
        """Return the reason of a generic, `Annotated` read by its first argument."""
        base = self.path_of(scope, node.value)
        items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base in _LITERAL:
            return None
        if base in _ANNOTATED:
            return self.reason(scope, items[0], seen)
        head = self._named(scope, node.value, seen, bare=False)
        return head or self._first(scope, items, seen)

    def _first(
        self, scope: _Scope, nodes: list[ast.expr], seen: frozenset[str]
    ) -> str | None:
        """Return the first reason any of `nodes` gives, in order."""
        for node in nodes:
            found = self.reason(scope, node, seen)
            if found is not None:
                return found
        return None


@dataclass(frozen=True, slots=True)
class _Service:
    """A public function of the services, by the name the module publishes it under."""

    source: SourceFile
    syntax: ast.Module
    name: str
    line: int
    node: ast.FunctionDef | ast.AsyncFunctionDef


def _published(syntax: ast.Module) -> Iterator[tuple[str, int, str]]:
    """Yield each public name the module binds to a function, its line and the function.

    A name assigned another function of the module, as `get = _get`, publishes it too.
    """
    for node in syntax.body:
        if isinstance(node, _FUNCTIONS):
            yield node.name, node.lineno, node.name
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            yield from (
                (target.id, node.lineno, node.value.id)
                for target in node.targets
                if isinstance(target, ast.Name)
            )


def _services(project: Project, label: str) -> Iterator[_Service]:
    """Yield each public function at the top of the services of the app `label`."""
    for source in project.files_of(label):
        if not within(source.app, source.path, "services"):
            continue
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        functions = {
            node.name: node for node in syntax.body if isinstance(node, _FUNCTIONS)
        }
        for name, line, bound in _published(syntax):
            node = functions.get(bound)
            if node is not None and not name.startswith("_"):
                yield _Service(source, syntax, name, line, node)


def _model_paths() -> frozenset[str]:
    """Return the dotted path of the class of every model the registry holds."""
    return frozenset(
        f"{model.__module__}.{model.__qualname__}"
        for model in django_apps.get_models(include_auto_created=True)
    )


def _service_findings(project: Project, reader: _Reader, app: App) -> Iterator[Finding]:
    """Yield what the public services of one isolated app return that they may not."""
    for service in _services(project, app.label):
        where = (project.relative(service.source.path), service.line)
        subject = f"The service {service.name} of the isolated app {app.label}"
        returns = service.node.returns
        if returns is None:
            message = f"{subject} has no return annotation."
            yield Finding(*where, UNANNOTATED_SERVICE, app.label, message)
            continue
        scope = reader.parsed(service.source, service.syntax)
        reason = reader.reason(scope, returns, frozenset())
        if reason is not None:
            message = f"{subject} returns {reason}."
            yield Finding(*where, ISOLATED_SERVICE, app.label, message)


def _publisher(module: str, published: dict[str, App]) -> App | None:
    """Return the isolated app whose services module or package holds `module`."""
    return next(
        (
            app
            for prefix, app in published.items()
            if module == prefix or module.startswith(f"{prefix}.")
        ),
        None,
    )


def _reexport_findings(
    project: Project, reader: _Reader, isolated: tuple[App, ...]
) -> Iterator[Finding]:
    """Yield each model or queryset another app imports from isolated services."""
    published = {f"{app.name}.services": app for app in isolated}
    for source in project.files:
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        for node in project.imports_from(source, syntax):
            module = absolute_module(node, source)
            owner = None if module is None else _publisher(module, published)
            if module is None or owner is None or owner == source.app:
                continue
            for alias in node.names:
                path = f"{module}.{alias.name}"
                if alias.name == "*" or path in project.modules:
                    continue
                leak = _LEAKS.get(reader.path_kind(path, frozenset()))
                if leak is None:
                    continue
                message = (
                    f"{source.app.label} imports {alias.name} from {module}, and "
                    f"{alias.name} is {leak} of the isolated app {owner.label}, ask "
                    "its services for a dataclass."
                )
                yield Finding(
                    project.relative(source.path),
                    node.lineno,
                    PRIVATE_IMPORT,
                    source.app.label,
                    message,
                )


def service_findings(project: Project, isolated: frozenset[str]) -> Iterator[Finding]:
    """Yield how the services of each isolated app let a model or a queryset out.

    A service may return none, and no other app may import one from the services.
    """
    apps = tuple(app for app in project.apps if app.label in isolated)
    if not apps:
        return
    reader = _Reader(project, _model_paths())
    for app in apps:
        yield from _service_findings(project, reader, app)
    yield from _reexport_findings(project, reader, apps)


__all__ = ["service_findings"]
