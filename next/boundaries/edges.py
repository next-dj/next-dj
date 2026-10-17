"""The edges between project applications, each found in a source or a registry.

Models, DI keys, zones and components come from the registries Django start filled.
"""

import ast
import inspect
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import CodeType
from typing import Final

from django.apps import apps as django_apps
from django.db import models

from next.components.sources import get_components_manager
from next.deps import resolver
from next.pages.checks.composed import iter_composed_pages
from next.partial.checks.nodes import zone_nodes

from .graph import App, Edge, EdgeKind, edge_order
from .imports import ImportScan
from .sources import (
    Project,
    SourceFile,
    absolute_module,
    argument,
    assigns,
    call_name,
    dotted,
    string,
)


_COMPONENT_TAG: Final = re.compile(r"""\{%\s*#?component\s+(["'])([^"']+)\1""")
_PERM_TAG: Final = re.compile(r"""\{%\s*has_perm\s+(["'])([^"']+)\1""")
_PERM_CALLS: Final = frozenset({"add_perm", "has_perm", "permission_required"})
_PERM_SETTING: Final = "permission_required"
_META: Final = "Meta"
_TEMPLATE: Final = "template"


@dataclass(frozen=True, slots=True)
class Registration:
    """A DI key and the application whose callable provides it."""

    key: str
    app: App
    file: Path
    line: int


def _edge(
    source: SourceFile, target: App | None, line: int, detail: str
) -> Edge | None:
    """Return an import edge to another application, `None` inside one."""
    if target is None or target == source.app:
        return None
    return Edge("import", source.app.label, target.label, source.path, line, detail)


def _statement_imports(
    project: Project, syntax: ast.Module, source: SourceFile
) -> Iterator[tuple[str, int]]:
    """Yield the module each import statement of a file reads, with its line."""
    for node in ast.walk(syntax):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = absolute_module(node, source)
            if base is None:
                continue
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                yield (candidate if candidate in project.modules else base), node.lineno


def escaping_import(project: Project) -> tuple[SourceFile, int] | None:
    """Return where a file grimp reads imports relatively above its package.

    grimp panics on such an import, so the scan names it before grimp reads anything.
    """
    for source in project.files:
        syntax = project.syntax(source.path) if source.packaged else None
        if syntax is None:
            continue
        for node in ast.walk(syntax):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level
                and absolute_module(node, source) is None
            ):
                return source, node.lineno
    return None


def import_edges(project: Project, scan: ImportScan) -> Iterator[Edge]:
    """Yield an edge per import of one application by another.

    The parser adds what grimp misses, the files and modules outside its packages.
    """
    for item in scan.imports:
        source = project.modules.get(item.importer)
        if source is None:
            continue
        target = project.app_of_module(item.imported)
        edge = _edge(source, target, item.line, item.imported)
        if edge is not None:
            yield edge
    for source in project.files:
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        scanned = source.module in scan.modules
        for module, line in _statement_imports(project, syntax, source):
            if scanned and (module in scan.modules or module not in project.modules):
                continue
            edge = _edge(source, project.app_of_module(module), line, module)
            if edge is not None:
                yield edge


def model_file(model: type[models.Model]) -> Path | None:
    """Return the source file of a model class, `None` when it has none."""
    module = sys.modules.get(model.__module__)
    file = getattr(module, "__file__", None)
    return Path(file).resolve() if isinstance(file, str) else None


def class_line(syntax: ast.Module | None, name: str, attribute: str = "") -> int:
    """Return the line of `attribute` in the class `name`, or of the class, or 0."""
    if syntax is None:
        return 0
    for node in ast.walk(syntax):
        if not isinstance(node, ast.ClassDef) or node.name != name:
            continue
        for statement in node.body:
            if attribute and assigns(statement, attribute):
                return statement.lineno
        return node.lineno
    return 0


def fk_edges(project: Project) -> Iterator[Edge]:
    """Yield an edge per relation field of a model pointing at another application."""
    labels = project.labels
    for app in project.apps:
        for model in django_apps.get_app_config(app.label).get_models():
            file = model_file(model)
            syntax = None if file is None else project.syntax(file)
            meta = model._meta
            for field in (*meta.local_fields, *meta.local_many_to_many):
                related = field.related_model
                if not isinstance(related, type) or not issubclass(
                    related, models.Model
                ):
                    continue
                target = related._meta.app_label
                if target == app.label or target not in labels:
                    continue
                yield Edge(
                    "fk",
                    app.label,
                    target,
                    file or app.path,
                    class_line(syntax, model.__name__, field.name),
                    f"{model.__name__}.{field.name} -> {related._meta.label}",
                )


def _code(func: Callable[..., object]) -> CodeType | None:
    """Return the code object of a callable or of what it wraps."""
    code = getattr(inspect.unwrap(func), "__code__", None)
    return code if isinstance(code, CodeType) else None


def registrations(project: Project) -> tuple[Registration, ...]:
    """Return each DI key the resolver holds and the application that provides it."""
    found: list[Registration] = []
    for key, func in sorted(resolver.dependencies().items()):
        code = _code(func)
        if code is not None:
            file = Path(code.co_filename).resolve()
            app = project.app_of(file)
            line = code.co_firstlineno
        else:
            app = project.app_of_module(getattr(func, "__module__", "") or "")
            file = app.path if app is not None else Path()
            line = 0
        if app is not None:
            found.append(Registration(key, app, file, line))
    return tuple(found)


def zone_owners(project: Project) -> dict[str, frozenset[str]]:
    """Map every zone name to the applications whose page templates declare it."""
    owners: dict[str, set[str]] = defaultdict(set)
    for page_path, template in iter_composed_pages():
        app = project.app_of(page_path.resolve())
        if app is None:
            continue
        for node in zone_nodes(template):
            owners[node.name].add(app.label)
    return {name: frozenset(labels) for name, labels in owners.items()}


@dataclass(frozen=True, slots=True)
class _Site:
    """One parsed file being read, with the names its imports bind."""

    project: Project
    source: SourceFile
    names: Mapping[str, str]

    def path_app(self, path: str) -> App | None:
        """Return the app the dotted `path` lies in."""
        return self.project.app_of_module(path)

    def label_app(self, label: str) -> App | None:
        """Return the app of `label`."""
        return self.project.app_by_label(label)

    def edge(
        self, kind: EdgeKind, app: App | None, line: int, detail: str
    ) -> Edge | None:
        """Return an edge to `app` from this file, `None` when it is the same app."""
        if app is None or app == self.source.app:
            return None
        source = self.source
        return Edge(kind, source.app.label, app.label, source.path, line, detail)


def _model_reference(call: ast.Call) -> tuple[str, str] | None:
    """Return the app label and detail of an `apps.get_model` written with literals."""
    label = string(argument(call, "app_label", 0))
    if label is None:
        return None
    second = argument(call, "model_name", 1)
    if second is None:
        if "." not in label:
            return None
        return label.partition(".")[0], label
    model = string(second)
    return label, label if model is None else f"{label}.{model}"


def _signal_targets(
    site: _Site, decorator: ast.Call
) -> Iterator[tuple[App | None, str]]:
    """Yield the app each thing a `@receiver` listens to belongs to, with a detail."""
    module = site.source.module
    first = decorator.args[0] if decorator.args else None
    signals = first.elts if isinstance(first, ast.List | ast.Tuple) else [first]
    for signal in signals:
        path = None if signal is None else dotted(signal, site.names, module)
        if path is not None:
            yield site.path_app(path), path
    sender = argument(decorator, "sender")
    if sender is None:
        return
    literal = string(sender)
    if literal is not None:
        yield site.label_app(literal.partition(".")[0]), f"sender={literal}"
        return
    path = dotted(sender, site.names, module)
    if path is not None:
        yield site.path_app(path), f"sender={path}"


def _receiver_edges(
    site: _Site, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> Iterator[Edge | None]:
    """Yield the signal edges the `@receiver` decorators of a function declare."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or call_name(decorator) != "receiver":
            continue
        for app, detail in _signal_targets(site, decorator):
            yield site.edge("signal", app, decorator.lineno, detail)


def _call_edge(
    site: _Site, call: ast.Call, providers: Mapping[str, App]
) -> Edge | None:
    """Return the `get_model` or `di` edge one call declares, if any."""
    name = call_name(call)
    if name == "get_model":
        reference = _model_reference(call)
        if reference is None:
            return None
        label, detail = reference
        return site.edge("get_model", site.label_app(label), call.lineno, detail)
    if name == "Depends":
        key = string(argument(call, "dependency", 0))
        if key is None:
            return None
        return site.edge("di", providers.get(key), call.lineno, key)
    return None


def _parameter_keys(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[tuple[str, int]]:
    """Yield the key of each bare `Depends()` default, the name of its parameter."""
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    pairs: list[tuple[ast.arg, ast.expr | None]] = list(
        zip(
            positional[len(positional) - len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        )
    )
    pairs.extend(zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True))
    for parameter, default in pairs:
        if (
            isinstance(default, ast.Call)
            and call_name(default) == "Depends"
            and not default.args
            and not default.keywords
        ):
            yield parameter.arg, default.lineno


def _strings(node: ast.expr) -> Iterator[str]:
    """Yield a string literal, or each string literal of a list or tuple."""
    if isinstance(node, ast.List | ast.Tuple):
        for item in node.elts:
            yield from _strings(item)
        return
    value = string(node)
    if value is not None:
        yield value


def _call_permissions(call: ast.Call) -> Iterator[ast.expr]:
    """Yield what a call may name permissions by, all of a permission call."""
    if call_name(call) in _PERM_CALLS:
        yield from call.args
        yield from (keyword.value for keyword in call.keywords)
        return
    yield from (
        keyword.value for keyword in call.keywords if keyword.arg == _PERM_SETTING
    )


def _permissions(syntax: ast.Module) -> Iterator[tuple[str, int]]:
    """Yield each permission a module names by a literal, with its line.

    A permission call, a `permission_required=` keyword and a `Meta` attribute count.
    """
    for node in ast.walk(syntax):
        if isinstance(node, ast.Call):
            for value in _call_permissions(node):
                yield from ((perm, node.lineno) for perm in _strings(value))
        elif isinstance(node, ast.ClassDef) and node.name == _META:
            for statement in node.body:
                if isinstance(statement, ast.Assign) and assigns(
                    statement, _PERM_SETTING
                ):
                    yield from (
                        (perm, statement.lineno) for perm in _strings(statement.value)
                    )


def _refreshed_zone(call: ast.Call) -> str | None:
    """Return the zone a `.refresh(zone="name")` call names with a literal."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "refresh":
        return None
    return string(argument(call, "zone"))


def source_edges(project: Project) -> Iterator[Edge]:
    """Yield the `get_model`, `signal`, `di`, `perm` and `zone` edges of the sources.

    The page templates are composed only when some file refreshes a zone by name.
    """
    providers = {item.key: item.app for item in registrations(project)}
    refreshes: list[tuple[_Site, int, str]] = []
    found: list[Edge | None] = []
    for source in project.files:
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        site = _Site(project, source, project.names(source, syntax))
        found.extend(
            site.edge("perm", project.foreign_label(perm, source.app), line, perm)
            for perm, line in _permissions(syntax)
        )
        for node in ast.walk(syntax):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.extend(_receiver_edges(site, node))
                found.extend(
                    site.edge("di", providers.get(key), line, key)
                    for key, line in _parameter_keys(node)
                )
            elif isinstance(node, ast.Call):
                found.append(_call_edge(site, node, providers))
                zone = _refreshed_zone(node)
                if zone is not None:
                    refreshes.append((site, node.lineno, zone))
    owners = zone_owners(project) if refreshes else {}
    for site, line, zone in refreshes:
        labels = owners.get(zone, frozenset())
        if site.source.app.label in labels:
            continue
        found.extend(
            site.edge("zone", site.label_app(label), line, zone)
            for label in sorted(labels)
        )
    yield from (edge for edge in found if edge is not None)


def component_owners(project: Project) -> dict[str, frozenset[str]]:
    """Map every component name to the applications whose trees register it."""
    owners: dict[str, set[str]] = defaultdict(set)
    for backend in get_components_manager().backends:
        for info in backend.iter_components():
            path = info.template_path or info.module_path or info.scope_root
            app = project.app_of(path.resolve())
            if app is not None:
                owners[info.name].add(app.label)
    return {name: frozenset(labels) for name, labels in owners.items()}


def _template_texts(project: Project) -> Iterator[tuple[App, Path, str, int]]:
    """Yield each template text of the apps, the line it starts on, and its file.

    A template file counts, and so does a literal `template` a module binds.
    """
    for app, path in project.templates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        yield app, path, text, 1
    for source in project.files:
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        for node in syntax.body:
            if not isinstance(node, ast.Assign) or not assigns(node, _TEMPLATE):
                continue
            value = string(node.value)
            if value is not None:
                yield source.app, source.path, value, node.value.lineno


def _matches(
    pattern: re.Pattern[str], text: str, first: int
) -> Iterator[tuple[int, str]]:
    """Yield the line and the quoted name of each match of a tag in a template."""
    for match in pattern.finditer(text):
        yield first + text.count("\n", 0, match.start()), match.group(2)


def template_edges(project: Project) -> Iterator[Edge]:
    """Yield the `perm` and `component` edges of the templates, rendering none.

    The components are looked up only when some template names one.
    """
    uses: list[tuple[App, Path, int, str]] = []
    for app, path, text, first in _template_texts(project):
        for line, perm in _matches(_PERM_TAG, text, first):
            target = project.foreign_label(perm, app)
            if target is not None:
                yield Edge("perm", app.label, target.label, path, line, perm)
        uses.extend(
            (app, path, line, name)
            for line, name in _matches(_COMPONENT_TAG, text, first)
        )
    owners = component_owners(project) if uses else {}
    for app, path, line, name in uses:
        found = owners.get(name, frozenset())
        if app.label in found:
            continue
        for label in sorted(found):
            yield Edge("component", app.label, label, path, line, name)


def collect_edges(project: Project, scan: ImportScan) -> tuple[Edge, ...]:
    """Return every edge between two project applications, once each, in order."""
    edges = {
        *import_edges(project, scan),
        *fk_edges(project),
        *source_edges(project),
        *template_edges(project),
    }
    return tuple(sorted(edges, key=edge_order))


__all__ = [
    "Registration",
    "class_line",
    "collect_edges",
    "component_owners",
    "escaping_import",
    "fk_edges",
    "import_edges",
    "model_file",
    "registrations",
    "source_edges",
    "template_edges",
    "zone_owners",
]
