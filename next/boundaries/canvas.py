"""The Module Canvas of one application, what it owns, publishes and leans on."""

import ast
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from django.apps import apps as django_apps

from next.permissions.endpoints import Endpoint, endpoints

from .edges import Registration
from .errors import UnknownAppError
from .graph import App, Edge, EdgeKind
from .lint import project_signals
from .manager import Scan
from .sources import Project, within


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """A model of the application and its relations to models of other apps."""

    name: str
    relations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Dependency:
    """Another application and how many edges of each kind join the two."""

    app: str
    kinds: tuple[tuple[EdgeKind, int], ...]


@dataclass(frozen=True, slots=True)
class Canvas:
    """Everything `manage.py next boundaries <app>` prints about one application."""

    app: App
    models: tuple[ModelEntry, ...]
    signals: tuple[str, ...]
    services: tuple[str, ...]
    listens: tuple[Edge, ...]
    keys: tuple[Registration, ...]
    pages: tuple[Endpoint, ...]
    risks: tuple[Endpoint, ...]
    depends_on: tuple[Dependency, ...]
    used_by: tuple[Dependency, ...]


def _models(app: App, outgoing: tuple[Edge, ...]) -> tuple[ModelEntry, ...]:
    """Return each model of the app with its relations that leave the app."""
    entries = []
    for model in django_apps.get_app_config(app.label).get_models():
        prefix = f"{model.__name__}."
        relations = tuple(
            edge.detail
            for edge in outgoing
            if edge.kind == "fk" and edge.detail.startswith(prefix)
        )
        entries.append(ModelEntry(model.__name__, relations))
    return tuple(sorted(entries, key=lambda entry: entry.name))


def _signals(project: Project, app: App) -> tuple[str, ...]:
    """Return the name of each signal the `signals.py` of the app declares."""
    prefix = f"{app.name}.signals"
    return tuple(
        path.removeprefix(prefix).lstrip(".")
        for path in sorted(project_signals(project))
        if project.app_of_module(path) == app and path.startswith(f"{prefix}.")
    )


def _services(project: Project, app: App) -> tuple[str, ...]:
    """Return each public function of the services module of the app."""
    names: list[str] = []
    prefix = f"{app.name}.services"
    for source in project.files_of(app.label):
        if not within(app, source.path, "services"):
            continue
        syntax = project.syntax(source.path)
        if syntax is None:
            continue
        module = source.module.removeprefix(prefix).lstrip(".")
        names.extend(
            f"{module}.{node.name}" if module else node.name
            for node in syntax.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and not node.name.startswith("_")
        )
    return tuple(sorted(names))


def _grouped(edges: Iterable[Edge], *, by_target: bool) -> tuple[Dependency, ...]:
    """Group edges by the other application, counting each kind."""
    counts: dict[str, Counter[EdgeKind]] = defaultdict(Counter)
    for edge in edges:
        counts[edge.target if by_target else edge.source][edge.kind] += 1
    return tuple(
        Dependency(label, tuple(sorted(counts[label].items())))
        for label in sorted(counts)
    )


def _risky(endpoint: Endpoint) -> bool:
    """Return whether an action is open to anyone with nothing declaring it so."""
    rule = endpoint.rule
    return (
        endpoint.kind == "action"
        and not rule.requirements
        and not rule.public
        and not endpoint.closed
    )


def canvas(scan: Scan, label: str) -> Canvas:
    """Return the Module Canvas of the application `label`."""
    graph = scan.graph
    app = graph.app(label)
    project = scan.project
    if app is None or project is None:
        raise UnknownAppError(label, tuple(item.label for item in graph.apps))
    outgoing = graph.outgoing(label)
    pages = tuple(
        endpoint
        for endpoint in endpoints()
        if endpoint.file.resolve().is_relative_to(app.path)
    )
    return Canvas(
        app=app,
        models=_models(app, outgoing),
        signals=_signals(project, app),
        services=_services(project, app),
        listens=tuple(edge for edge in outgoing if edge.kind == "signal"),
        keys=tuple(item for item in scan.registrations if item.app == app),
        pages=pages,
        risks=tuple(endpoint for endpoint in pages if _risky(endpoint)),
        depends_on=_grouped(outgoing, by_target=True),
        used_by=_grouped(graph.incoming(label), by_target=False),
    )


__all__ = ["Canvas", "Dependency", "ModelEntry", "canvas"]
