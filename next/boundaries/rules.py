"""The boundary rules, one row per check id, and the findings the graph breaks.

Every id, level, hint and doctrine anchor lives in this one table, edited in one place.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from .edges import Registration
from .errors import INSTALL_HINT, OFF_MESSAGE
from .graph import Edge, Graph, Tangle
from .sources import Project, within


type Level = Literal["error", "warning", "info"]

CYCLE: Final = "next.E167"
GROWN: Final = "next.E168"
STALE: Final = "next.E169"
BASELINE: Final = "next.E170"
UNRECORDED: Final = "next.E171"
SCAN: Final = "next.E173"
PRIVATE_IMPORT: Final = "next.W136"
TREE_IMPORT: Final = "next.W137"
UNSCOPED_QUERY: Final = "next.W138"
TREE_WRITE: Final = "next.W139"
FOREIGN_MODEL: Final = "next.W140"
FOREIGN_KEY_NAME: Final = "next.W141"
STRAY_RECEIVER: Final = "next.W142"
STRAY_SEND: Final = "next.W143"
FOREIGN_ZONE: Final = "next.W144"
PACKAGE_MODEL: Final = "next.W145"
AGGREGATOR: Final = "next.W146"
FOREIGN_FILE: Final = "next.W147"
STRICT_OFF: Final = "next.W148"
FOREIGN_PERMISSION: Final = "next.W149"
DISABLED: Final = "next.I001"

DOCTRINE: Final = (
    "https://next-dj.readthedocs.io/en/latest/content/topics/boundaries.html"
)
PUBLIC_MODULES: Final = frozenset({"models", "services", "signals"})
CORE_KEYS: Final = frozenset({"scope"})
REFREEZE_HINT: Final = "Run manage.py next boundaries --refreeze."

_KEY_PREFIX = re.compile(r"[_.]")


@dataclass(frozen=True, slots=True)
class Rule:
    """One boundary check id, its level, its hint and its section of the doctrine."""

    code: str
    level: Level
    hint: str
    anchor: str

    @property
    def doc(self) -> str:
        """Return the address of the doctrine section the rule points to."""
        return f"{DOCTRINE}#{self.anchor}"

    @property
    def full_hint(self) -> str:
        """Return the hint followed by where the doctrine explains the rule."""
        return f"{self.hint} See {self.doc}."


RULES: Final[Mapping[str, Rule]] = {
    rule.code: rule
    for rule in (
        Rule(
            CYCLE,
            "error",
            "Cut one edge of the cycle, usually by moving the shared code down.",
            "no-cycles",
        ),
        Rule(
            GROWN,
            "error",
            "Fix the new findings, since boundaries.toml only records old debt.",
            "ratchet",
        ),
        Rule(STALE, "error", REFREEZE_HINT, "ratchet"),
        Rule(
            BASELINE,
            "error",
            "Fix the file by hand, or delete it and run manage.py next boundaries "
            "--refreeze.",
            "ratchet",
        ),
        Rule(
            UNRECORDED,
            "error",
            "Fix the findings, or record the debt with manage.py next boundaries "
            "--refreeze.",
            "strict-mode",
        ),
        Rule(SCAN, "error", "Fix the file the message names.", "import-graph"),
        Rule(
            PRIVATE_IMPORT,
            "warning",
            "Import from the models, services or signals module of the other app, or "
            "move what you need there.",
            "public-modules",
        ),
        Rule(
            TREE_IMPORT,
            "warning",
            "Render a component of another app with {% component %}, and import "
            "shared code from its services module.",
            "page-trees",
        ),
        Rule(
            UNSCOPED_QUERY,
            "warning",
            "Narrow the queryset with .objects.visible_to(...), or ask a function of "
            "services.py.",
            "visible-to",
        ),
        Rule(
            TREE_WRITE,
            "warning",
            "Save through a form, or move the write to a function of services.py.",
            "writes",
        ),
        Rule(
            FOREIGN_MODEL,
            "warning",
            "Import the model from the models module of its app, or ask its services.",
            "get-model",
        ),
        Rule(
            FOREIGN_KEY_NAME,
            "warning",
            "Name a DI key after the app that provides it.",
            "di-keys",
        ),
        Rule(
            STRAY_RECEIVER,
            "warning",
            "Move the receiver to the signals.py of the app.",
            "receivers",
        ),
        Rule(
            STRAY_SEND,
            "warning",
            "Send the signal from the services function whose change it announces.",
            "sending-signals",
        ),
        Rule(
            FOREIGN_ZONE,
            "warning",
            "Refresh only zones of your own pages, and listen to a signal of the other "
            "app for the rest.",
            "zones",
        ),
        Rule(PACKAGE_MODEL, "warning", "Define models in models.py.", "models"),
        Rule(
            AGGREGATOR,
            "warning",
            "Import the name from its own app where it is used.",
            "aggregators",
        ),
        Rule(
            FOREIGN_FILE,
            "warning",
            "Use the module next.dj reads for that role.",
            "file-names",
        ),
        Rule(
            STRICT_OFF,
            "warning",
            "Install the next.dj[boundaries] extra and point BASE_DIR at the project.",
            "installing",
        ),
        Rule(
            FOREIGN_PERMISSION,
            "warning",
            "Register a permission in the permissions.py of the app its label names.",
            "foreign-permissions",
        ),
        Rule(DISABLED, "info", INSTALL_HINT, "installing"),
    )
}

RECORDABLE: Final = frozenset(
    code
    for code, rule in RULES.items()
    if (rule.level == "warning" and code != STRICT_OFF) or code == CYCLE
)


@dataclass(frozen=True, slots=True, order=True)
class Finding:
    """One broken rule, its app and where it is, the file relative to `BASE_DIR`."""

    file: str
    line: int
    code: str
    app: str
    message: str


def public(project: Project, module: str) -> bool:
    """Return whether another app may import the dotted `module`."""
    app = project.app_of_module(module)
    if app is None:
        return True
    head = app.submodule(module).partition(".")[0]
    return head in PUBLIC_MODULES


def _finding(project: Project, code: str, edge: Edge, message: str) -> Finding:
    """Return the finding one edge breaks, at the file and line of the edge."""
    return Finding(project.relative(edge.file), edge.line, code, edge.source, message)


def _import_finding(project: Project, edge: Edge) -> Finding | None:
    """Return the import rule an edge breaks, the page tree rule first."""
    module = edge.detail
    if project.in_tree(module):
        message = f"{edge.source} imports {module} from a page tree of {edge.target}."
        return _finding(project, TREE_IMPORT, edge, message)
    if public(project, module):
        return None
    message = (
        f"{edge.source} imports {module}, which {edge.target} does not publish. "
        "Only its models, services and signals modules are public."
    )
    return _finding(project, PRIVATE_IMPORT, edge, message)


def _in_signals(edge: Edge, project: Project) -> bool:
    """Return whether the file of an edge is the signals module of its app."""
    app = project.app_of(edge.file)
    return app is not None and within(app, edge.file, "signals")


def _edge_finding(project: Project, edge: Edge) -> Finding | None:
    """Return the rule one edge breaks, `None` for an edge no rule forbids."""
    if edge.kind == "import":
        return _import_finding(project, edge)
    if edge.kind == "get_model":
        message = (
            f"{edge.source} loads the model {edge.detail} of {edge.target} "
            "with apps.get_model."
        )
        return _finding(project, FOREIGN_MODEL, edge, message)
    if edge.kind == "signal" and not _in_signals(edge, project):
        message = (
            f"{edge.source} receives {edge.detail} of {edge.target} outside signals.py."
        )
        return _finding(project, STRAY_RECEIVER, edge, message)
    if edge.kind == "zone":
        message = f"{edge.source} refreshes the zone {edge.detail!r} of {edge.target}."
        return _finding(project, FOREIGN_ZONE, edge, message)
    return None


def _tangle_findings(project: Project, tangle: Tangle) -> Iterator[Finding]:
    """Yield one finding per pair of apps in a tangle, at the first edge of the pair.

    A new app or a new pair in a tangle is a new finding the ratchet does not cover.
    """
    cycle = tangle.cycle
    route = " -> ".join([*(edge.source for edge in cycle), cycle[0].source])
    steps = ", ".join(
        f"{edge.source} -> {edge.target} by {edge.kind} {edge.detail}" for edge in cycle
    )
    labels = ", ".join(tangle.labels)
    for edge in tangle.pairs:
        message = (
            f"{edge.source} depends on {edge.target} by {edge.kind} {edge.detail}, "
            f"and the apps {labels} depend on each other in a cycle, as in {route} "
            f"({steps})."
        )
        yield _finding(project, CYCLE, edge, message)


def _key_findings(
    project: Project, registrations: Iterable[Registration]
) -> Iterator[Finding]:
    """Yield a finding per DI key named after an app other than its provider."""
    labels = {app.label for app in project.apps}
    for item in registrations:
        prefix = _KEY_PREFIX.split(item.key, maxsplit=1)[0]
        if item.key in CORE_KEYS or prefix not in labels or prefix == item.app.label:
            continue
        yield Finding(
            project.relative(item.file),
            item.line,
            FOREIGN_KEY_NAME,
            item.app.label,
            f"{item.app.label} provides the DI key {item.key!r}, named after the app "
            f"{prefix}.",
        )


def graph_findings(
    project: Project, graph: Graph, registrations: Iterable[Registration]
) -> Iterator[Finding]:
    """Yield the findings of the edges, the tangles and the DI key names."""
    for edge in graph.edges:
        found = _edge_finding(project, edge)
        if found is not None:
            yield found
    for tangle in graph.tangles():
        yield from _tangle_findings(project, tangle)
    yield from _key_findings(project, registrations)


__all__ = [
    "AGGREGATOR",
    "BASELINE",
    "CYCLE",
    "DISABLED",
    "DOCTRINE",
    "FOREIGN_FILE",
    "FOREIGN_KEY_NAME",
    "FOREIGN_MODEL",
    "FOREIGN_PERMISSION",
    "FOREIGN_ZONE",
    "GROWN",
    "INSTALL_HINT",
    "OFF_MESSAGE",
    "PACKAGE_MODEL",
    "PRIVATE_IMPORT",
    "PUBLIC_MODULES",
    "RECORDABLE",
    "REFREEZE_HINT",
    "RULES",
    "SCAN",
    "STALE",
    "STRAY_RECEIVER",
    "STRAY_SEND",
    "STRICT_OFF",
    "TREE_IMPORT",
    "TREE_WRITE",
    "UNRECORDED",
    "UNSCOPED_QUERY",
    "Finding",
    "Level",
    "Rule",
    "graph_findings",
    "public",
]
