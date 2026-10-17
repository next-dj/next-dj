"""`manage.py next boundaries`, the boundary debt of a project or one app canvas."""

from argparse import ArgumentParser
from collections import defaultdict
from typing import override

from django.core.management.base import BaseCommand, CommandError

from next.boundaries.canvas import Canvas, Dependency, canvas
from next.boundaries.errors import BaselineError, ImportScanError, UnknownAppError
from next.boundaries.manager import Refrozen, Report, boundaries_manager
from next.boundaries.rules import BASELINE, RULES, SCAN
from next.management.subcommands import (
    ENDPOINT_HEADER,
    BaseSubcommand,
    endpoint_row,
    relative,
    table,
)
from next.utils import resolve_base_dir


_DEBT_HEADER = ("App", "Code", "Found", "Recorded")
_NONE = "  -"
_BROKEN = frozenset({BASELINE, SCAN})


class Subcommand(BaseSubcommand):
    """Print the boundary debt, the Module Canvas of one app, or refreeze the record."""

    help = (
        "Show the boundary debt of every app against boundaries.toml, the Module "
        "Canvas of one app, or refreeze boundaries.toml."
    )
    skips_checks = True

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Take an optional app label and the `--refreeze` switch."""
        parser.add_argument(
            "app", nargs="?", help="The label of one app, to print its canvas."
        )
        parser.add_argument(
            "--refreeze",
            action="store_true",
            help="Write boundaries.toml, all findings with no file, else only shrink.",
        )

    @override
    def handle(self, command: BaseCommand, **options: object) -> None:
        """Run the mode the options pick, as a `CommandError` when it cannot."""
        label = options.get("app")
        off = boundaries_manager.disabled()
        if off is not None:
            msg = f"{off.message} {RULES[off.code].hint}"
            raise CommandError(msg)
        try:
            if options.get("refreeze"):
                if label is not None:
                    msg = "--refreeze records the whole project and takes no app."
                    raise CommandError(msg)
                _refreeze(command, boundaries_manager.refreeze())
            elif isinstance(label, str):
                _canvas(command, canvas(boundaries_manager.scan(), label))
            else:
                _debt(command, boundaries_manager.report())
        except (BaselineError, ImportScanError, UnknownAppError) as exc:
            raise CommandError(str(exc)) from exc


def _debt(command: BaseCommand, report: Report) -> None:
    """Write the found and recorded findings per app and code, then the totals."""
    for finding in report.findings:
        if finding.code in _BROKEN:
            raise CommandError(finding.message)
    totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for debt in report.debts:
        row = totals[debt.app or "-", debt.code]
        row[0] += debt.found
        row[1] += debt.recorded
    if not totals:
        command.stdout.write(f"No boundary findings. Mode: {report.mode}.")
        return
    rows = [
        (app, code, str(found), str(recorded))
        for (app, code), (found, recorded) in sorted(totals.items())
    ]
    table(command, _DEBT_HEADER, rows)
    found = sum(row[0] for row in totals.values())
    recorded = sum(row[1] for row in totals.values())
    command.stdout.write(
        f"Findings: {found}, recorded in boundaries.toml: {recorded}. "
        f"Mode: {report.mode}."
    )


def _section(command: BaseCommand, title: str, lines: list[str]) -> None:
    """Write one canvas section, a dash when it holds nothing."""
    command.stdout.write("")
    command.stdout.write(command.style.MIGRATE_HEADING(title))
    for line in lines or [_NONE]:
        command.stdout.write(line)


def _dependencies(found: tuple[Dependency, ...]) -> list[str]:
    """Spell each other app with the count of every edge kind joining them."""
    return [
        f"  {item.app}: " + ", ".join(f"{kind} x{count}" for kind, count in item.kinds)
        for item in found
    ]


def _canvas(command: BaseCommand, found: Canvas) -> None:
    """Write the Module Canvas of one app, section by section."""
    base_dir = resolve_base_dir()
    app = found.app
    command.stdout.write(
        command.style.SUCCESS(f"{app.label} ({relative(app.path, base_dir)})")
    )
    models = [
        f"  {entry.name}"
        + (f" ({', '.join(entry.relations)})" if entry.relations else "")
        for entry in found.models
    ]
    _section(command, "Models", models)
    publishes = [f"  signal {name}" for name in found.signals]
    publishes.extend(f"  service {name}" for name in found.services)
    _section(command, "Publishes", publishes)
    listens = [
        f"  {edge.detail} of {edge.target} "
        f"({relative(edge.file, base_dir)}:{edge.line})"
        for edge in found.listens
    ]
    _section(command, "Listens", listens)
    keys = [
        f"  {item.key} ({relative(item.file, base_dir)}:{item.line})"
        for item in found.keys
    ]
    _section(command, "DI keys", keys)
    command.stdout.write("")
    command.stdout.write(command.style.MIGRATE_HEADING("Pages"))
    if found.pages:
        rows = [endpoint_row(endpoint, base_dir) for endpoint in found.pages]
        table(command, ENDPOINT_HEADER, rows)
    else:
        command.stdout.write(_NONE)
    risks = [
        f"  {endpoint.name} ({relative(endpoint.file, base_dir)}) is open to anyone, "
        "and nothing declares it public."
        for endpoint in found.risks
    ]
    _section(command, "Risks", risks)
    _section(command, "Depends on", _dependencies(found.depends_on))
    _section(command, "Used by", _dependencies(found.used_by))


def _refreeze(command: BaseCommand, refrozen: Refrozen) -> None:
    """Write what a refreeze recorded and what it left for the project to fix."""
    verb = "Created" if refrozen.created else "Shrank"
    command.stdout.write(
        f"{verb} {refrozen.path}. Entries: {refrozen.entries}, findings: "
        f"{refrozen.recorded}."
    )
    if refrozen.unrecorded:
        command.stdout.write(
            f"Findings left unrecorded: {refrozen.unrecorded}. A refreeze only shrinks "
            "an existing file, so fix them, or delete boundaries.toml and refreeze "
            "again to record them."
        )
