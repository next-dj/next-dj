"""The façade over the boundary graph, its findings and the `boundaries.toml` ratchet.

The scan is built on first use and kept for the rest of the check run.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from next.checks.common import RunMemo
from next.conf import next_framework_settings
from next.conf.defaults import STRICT, WARN
from next.discovery import get_router_manager
from next.utils import resolve_base_dir

from . import imports
from .baseline import FILENAME, Debt, baseline_path, compare, read, shrink, write
from .edges import Registration, collect_edges, escaping_import, registrations
from .errors import OFF_MESSAGE, BaselineError, ImportScanError
from .graph import Graph, project_apps
from .lint import source_findings
from .rules import (
    BASELINE,
    DISABLED,
    RULES,
    SCAN,
    STRICT_OFF,
    UNRECORDED,
    Finding,
    graph_findings,
)
from .signals import baseline_refrozen
from .sources import Project, load_project


@dataclass(frozen=True, slots=True)
class Scan:
    """One scan of the project, its graph, its DI keys and the findings of both."""

    project: Project | None
    graph: Graph
    registrations: tuple[Registration, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class Report:
    """What a check shows once the ratchet has covered what it records."""

    mode: str
    findings: tuple[Finding, ...]
    debts: tuple[Debt, ...]


@dataclass(frozen=True, slots=True)
class Refrozen:
    """What a refreeze wrote and what it left out."""

    path: Path
    created: bool
    entries: int
    recorded: int
    unrecorded: int


_EMPTY: Final = Scan(None, Graph((), ()), (), ())

_ESCAPING: Final = "The relative import reaches above its package."

_scans: RunMemo[Scan] = RunMemo()


def _build() -> Scan:
    """Scan the project applications, or answer an empty scan without `BASE_DIR`."""
    base_dir = resolve_base_dir()
    apps = project_apps(base_dir)
    if base_dir is None or not apps:
        return _EMPTY
    project = load_project(base_dir, apps)
    escaping = escaping_import(project)
    if escaping is not None:
        source, line = escaping
        raise ImportScanError(_ESCAPING, project.relative(source.path), line)
    scan = imports.package_imports(app.name.partition(".")[0] for app in apps)
    for source in project.files:
        error = project.syntax_error(source.path)
        if error is not None and source.module not in scan.modules:
            line, reason = error
            raise ImportScanError(reason, project.relative(source.path), line)
    graph = Graph(apps, collect_edges(project, scan))
    held = registrations(project)
    findings = sorted(
        [*graph_findings(project, graph, held), *source_findings(project)]
    )
    return Scan(project, graph, held, tuple(findings))


class BoundariesManager:
    """Builds the boundary graph lazily and judges its findings against the ratchet."""

    @property
    def available(self) -> bool:
        """Return whether grimp is installed, without which the boundaries are off."""
        return imports.AVAILABLE

    @property
    def mode(self) -> str:
        """Return the mode `BOUNDARIES` names, the warn mode for any other value."""
        mode = next_framework_settings.BOUNDARIES
        return STRICT if mode == STRICT else WARN

    @property
    def strict(self) -> bool:
        """Return whether `NEXT_FRAMEWORK["BOUNDARIES"]` asks for the strict mode."""
        return self.mode == STRICT

    def disabled(self) -> Finding | None:
        """Return why the boundaries go unchecked, `None` when they are checked.

        The strict mode says so as a warning, so a CI failing on warnings notices.
        """
        available = imports.AVAILABLE
        if available and not (self.strict and resolve_base_dir() is None):
            return None
        if not self.strict:
            return Finding("", 0, DISABLED, "", OFF_MESSAGE)
        reason = "BASE_DIR is not set" if available else "grimp is not installed"
        return Finding(
            "",
            0,
            STRICT_OFF,
            "",
            f"NEXT_FRAMEWORK['BOUNDARIES'] is {STRICT!r}, but the boundaries go "
            f"unchecked, because {reason}.",
        )

    def reset(self) -> None:
        """Drop the held scan, so the next call reads the sources again."""
        _scans.forget()

    def scan(self) -> Scan:
        """Return the scan of this check run, building it on first use.

        Without grimp the import scan raises `BoundariesUnavailableError`.
        """
        router_manager, _errors = get_router_manager()
        return _scans.get(router_manager, _build)

    def graph(self) -> Graph:
        """Return the applications of the project and the edges between them."""
        return self.scan().graph

    def findings(self) -> tuple[Finding, ...]:
        """Return every finding of the project, before the ratchet covers any."""
        return self.scan().findings

    def _app_of(self, file: str) -> str:
        """Return the label of the app holding a file relative to `BASE_DIR`."""
        project = self.scan().project
        if project is None:
            return ""
        app = project.app_of((project.base_dir / file).resolve())
        return "" if app is None else app.label

    def report(self) -> Report:
        """Return the findings a check shows and the debt of every file and code.

        A broken import scan stands alone, since every rule reads the graph it stops.
        """
        mode = self.mode
        try:
            findings = self.findings()
        except ImportScanError as exc:
            found = Finding(exc.file, exc.line, SCAN, "", str(exc))
            return Report(mode, (found,), ())
        base_dir = resolve_base_dir()
        problems: list[Finding] = []
        recorded = None
        if base_dir is not None:
            try:
                recorded = read(baseline_path(base_dir))
            except BaselineError as exc:
                problems.append(Finding(FILENAME, 0, BASELINE, "", str(exc)))
        ratchet = compare(findings, recorded, self._app_of)
        problems.extend(ratchet.problems)
        unrecorded = sum(
            1 for finding in ratchet.shown if RULES[finding.code].level == "warning"
        )
        if mode == STRICT and unrecorded:
            problems.append(
                Finding(
                    FILENAME,
                    0,
                    UNRECORDED,
                    "",
                    f"Findings missing from boundaries.toml: {unrecorded}, and "
                    f"NEXT_FRAMEWORK['BOUNDARIES'] is {STRICT!r}.",
                )
            )
        return Report(mode, (*ratchet.shown, *problems), ratchet.debts)

    def refreeze(self) -> Refrozen:
        """Write the ratchet, all findings with no file and only shrinking with one."""
        base_dir = resolve_base_dir()
        if base_dir is None:
            raise BaselineError(None, "BASE_DIR is not set, so it has no place.")
        findings = self.findings()
        path = baseline_path(base_dir)
        recorded = read(path)
        counts, unrecorded = shrink(findings, recorded)
        write(path, counts)
        refrozen = Refrozen(
            path=path,
            created=recorded is None,
            entries=len(counts),
            recorded=sum(counts.values()),
            unrecorded=unrecorded,
        )
        baseline_refrozen.send(sender=type(self), refrozen=refrozen)
        return refrozen


boundaries_manager: BoundariesManager = BoundariesManager()


__all__ = ["BoundariesManager", "Refrozen", "Report", "Scan", "boundaries_manager"]
