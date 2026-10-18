"""What each project app declares about its boundaries on its `AppConfig`.

One reader checks every attribute, so a broken one is refused once and spreads nowhere.
"""

import os
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from django.apps import AppConfig, apps as django_apps
from django.conf import ENVIRONMENT_VARIABLE, settings

from next.utils import resolve_base_dir

from .edges import class_line
from .graph import Edge, Graph, edge_order, project_apps
from .rules import CLOSURE, DECLARATIONS, UNDECLARED, Finding
from .sources import Project, class_file, parse, posix_relative


DEPENDS_ON: Final = "depends_on"
_ISOLATED: Final = "isolated"


@dataclass(frozen=True, slots=True)
class Declaration:
    """The checked boundary attributes of one project app.

    `depends_on` is `None` when the app declares none, or declares it wrongly.
    """

    label: str
    name: str
    depends_on: tuple[str, ...] | None
    isolated: bool


@dataclass(frozen=True, slots=True)
class Declarations:
    """The declarations of every project app and the findings of the broken ones."""

    apps: tuple[Declaration, ...]
    findings: tuple[Finding, ...]

    def of(self, label: str) -> Declaration | None:
        """Return the declaration of the app `label`, `None` for no project app."""
        return next((item for item in self.apps if item.label == label), None)

    @property
    def isolated(self) -> frozenset[str]:
        """Return the label of every app that declares itself isolated."""
        return frozenset(item.label for item in self.apps if item.isolated)


@dataclass(frozen=True, slots=True)
class Unused:
    """The apps that declare `depends_on` and the entries no edge of theirs uses.

    Each entry is the label of the declaring app and the name it declares.
    """

    declaring: tuple[str, ...]
    entries: tuple[tuple[str, str], ...]


NO_DECLARATIONS: Final = Declarations((), ())


def _where(config: AppConfig, attribute: str) -> tuple[str, int]:
    """Return the file and line of an attribute on the class of `config` defining it."""
    mro = type(config).__mro__
    cls = next((klass for klass in mro if attribute in vars(klass)), type(config))
    path = class_file(cls)
    if path is None:
        return "", 0
    syntax, _error = parse(path)
    base_dir = resolve_base_dir()
    shown = posix_relative(path, None if base_dir is None else base_dir.resolve())
    return shown, class_line(syntax, cls.__name__, attribute)


def _depends_on(config: AppConfig) -> tuple[tuple[str, ...] | None, str | None]:
    """Return the names `depends_on` declares, or why the value is refused."""
    value: object = getattr(config, DEPENDS_ON, None)
    if value is None:
        return None, None
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        return None, "declares depends_on as something other than a tuple of app names"
    names: tuple[str, ...] = value
    if "" in names:
        return None, "lists an empty app name in depends_on"
    repeated = sorted(name for name, count in Counter(names).items() if count > 1)
    if repeated:
        return None, f"lists {', '.join(repeated)} in depends_on more than once"
    if config.name in names:
        return None, "lists itself in depends_on"
    return names, None


def _isolated(config: AppConfig) -> tuple[bool, str | None]:
    """Return whether the app declares itself isolated, or why the value is refused."""
    value: object = getattr(config, _ISOLATED, False)
    if isinstance(value, bool):
        return value, None
    return False, f"declares isolated as {type(value).__name__} instead of a bool"


def _settings_module() -> str:
    """Return the settings the process runs under with its verb, for E174 to quote."""
    named = os.environ.get(ENVIRONMENT_VARIABLE) or settings.SETTINGS_MODULE
    return f"{named} does" if named else "these settings do"


def _named_by_label(label: str) -> str | None:
    """Return the name of the installed app whose label is `label`, if any."""
    try:
        return django_apps.get_app_config(label).name
    except LookupError:
        return None


def _missing(label: str, name: str, installs: str) -> str:
    """Return why the app `label` misses `name`, telling an app label from a name."""
    named = _named_by_label(name)
    if named is None:
        return f"{label} depends on {name}, which {installs} not install."
    return (
        f"{label} depends on {name}, the label of {named}, and depends_on takes app "
        "names."
    )


def dropped(declarations: Declarations) -> tuple[str, ...]:
    """Return each app name a `depends_on` lists that the settings leave out.

    An installed app written by its label is a mistake of the entry, not a dropped app.
    """
    return tuple(
        sorted(
            {
                name
                for item in declarations.apps
                for name in item.depends_on or ()
                if not django_apps.is_installed(name) and _named_by_label(name) is None
            }
        )
    )


def read_declarations() -> Declarations:
    """Read and check the boundary attributes of every app of the project.

    An app outside `BASE_DIR` may mean something else by the same names, so it is left.
    """
    found: list[Declaration] = []
    findings: list[Finding] = []
    installs = _settings_module()
    configs = (
        django_apps.get_app_config(app.label)
        for app in project_apps(resolve_base_dir())
    )
    for config in configs:
        depends_on, refused_depends = _depends_on(config)
        isolated, refused_isolated = _isolated(config)
        for attribute, reason in (
            (DEPENDS_ON, refused_depends),
            (_ISOLATED, refused_isolated),
        ):
            if reason is not None:
                file, line = _where(config, attribute)
                message = f"{config.label} {reason}."
                findings.append(
                    Finding(file, line, DECLARATIONS, config.label, message)
                )
        for name in depends_on or ():
            if not django_apps.is_installed(name):
                file, line = _where(config, DEPENDS_ON)
                message = _missing(config.label, name, installs)
                findings.append(Finding(file, line, CLOSURE, config.label, message))
        found.append(Declaration(config.label, config.name, depends_on, isolated))
    return Declarations(tuple(found), tuple(sorted(findings)))


def undeclared(
    graph: Graph, declared: Declaration | None
) -> dict[str, tuple[Edge, ...]]:
    """Map each app this one leans on that its `depends_on` leaves out to the edges.

    The edges go in order, and an app without `depends_on` leaves nothing out.
    """
    if declared is None or declared.depends_on is None:
        return {}
    names = graph.names
    pairs: dict[str, list[Edge]] = defaultdict(list)
    for edge in sorted(graph.outgoing(declared.label), key=edge_order):
        if names[edge.target] not in declared.depends_on:
            pairs[edge.target].append(edge)
    return {target: tuple(edges) for target, edges in sorted(pairs.items())}


def undeclared_findings(
    project: Project, graph: Graph, declarations: Declarations
) -> Iterator[Finding]:
    """Yield one finding per app another one depends on without declaring it.

    It stands at the first edge of the pair and counts every edge of each kind.
    """
    names = graph.names
    for app in graph.apps:
        for target, edges in undeclared(graph, declarations.of(app.label)).items():
            kinds = Counter(edge.kind for edge in edges)
            spelled = ", ".join(
                f"{kind} x{count}" for kind, count in sorted(kinds.items())
            )
            first = edges[0]
            yield Finding(
                project.relative(first.file),
                first.line,
                UNDECLARED,
                app.label,
                f"{app.label} depends on {target} by {spelled}, and its depends_on "
                f"does not declare {names[target]}.",
            )


def unused_declarations(graph: Graph, declarations: Declarations) -> Unused:
    """Return the `depends_on` entries naming a project app no edge leads to.

    An installed app outside the project has no node, so its entries never count.
    """
    labels = {name: label for label, name in graph.names.items()}
    declaring: list[str] = []
    entries: list[tuple[str, str]] = []
    for app in graph.apps:
        declared = declarations.of(app.label)
        if declared is None or declared.depends_on is None:
            continue
        declaring.append(app.label)
        targets = {edge.target for edge in graph.outgoing(app.label)}
        entries.extend(
            (app.label, name)
            for name in declared.depends_on
            if name in labels and labels[name] not in targets
        )
    return Unused(tuple(declaring), tuple(entries))


__all__ = [
    "DEPENDS_ON",
    "NO_DECLARATIONS",
    "Declaration",
    "Declarations",
    "Unused",
    "dropped",
    "read_declarations",
    "undeclared",
    "undeclared_findings",
    "unused_declarations",
]
