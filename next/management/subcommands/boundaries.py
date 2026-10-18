"""`manage.py next boundaries`, the boundary debt of a project or one app canvas.

`sync` as the first word writes `depends_on`, and an app labelled `sync` goes by name.
"""

from argparse import ArgumentParser
from collections import defaultdict
from typing import override

from django.core.management.base import BaseCommand, CommandError

from next.boundaries.canvas import Canvas, Dependency, canvas
from next.boundaries.declared import unused_declarations
from next.boundaries.errors import (
    BaselineError,
    ImportGraphError,
    ImportScanError,
    SyncError,
    UnknownAppError,
)
from next.boundaries.kernel import kernels
from next.boundaries.manager import Refrozen, Report, Scan, boundaries_manager
from next.boundaries.rules import BASELINE, IMPORT_GRAPH, RULES, SCAN, Finding
from next.boundaries.sync import apply, plan
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
_BROKEN = frozenset({BASELINE, IMPORT_GRAPH, SCAN})
_OFF = "Boundaries are off, set NEXT_FRAMEWORK['BOUNDARIES'] to 'warn' or 'strict'."
_SYNC = "sync"


class Subcommand(BaseSubcommand):
    """Print the boundary debt, the Module Canvas of one app, or refreeze the record.

    `sync` adds to the `depends_on` of each app the apps its edges reach.
    """

    help = (
        "Show the boundary debt of every app against boundaries.toml, the Module "
        "Canvas of one app, refreeze boundaries.toml, or sync depends_on."
    )
    skips_checks = True

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Take an app or `sync` with an optional app, and the switches."""
        parser.add_argument(
            "app",
            nargs="?",
            help="The label or name of one app, to print its canvas, or sync.",
        )
        parser.add_argument(
            "sync_app",
            nargs="?",
            help="After sync, the label or name of the one app to sync.",
        )
        parser.add_argument(
            "--refreeze",
            action="store_true",
            help="Write boundaries.toml, all findings with no file, else only shrink.",
        )
        parser.add_argument(
            "--exact",
            action="store_true",
            help="Also list each depends_on entry no edge uses, failing when any is.",
        )
        parser.add_argument(
            "--add",
            action="store_true",
            help="With sync, write the names it would add instead of showing them.",
        )

    @override
    def handle(self, command: BaseCommand, **options: object) -> None:
        """Run the mode the options pick, as a `CommandError` when it cannot."""
        first, second = options.get("app"), options.get("sync_app")
        syncing = first == _SYNC
        _refuse(options, syncing=syncing)
        if not boundaries_manager.enabled:
            raise CommandError(_OFF)
        off = boundaries_manager.disabled()
        if off is not None:
            msg = f"{off.message} {RULES[off.code].hint}"
            raise CommandError(msg)
        try:
            if syncing:
                key = second if isinstance(second, str) else None
                _sync(command, boundaries_manager.scan(), key, add=bool(options["add"]))
            elif options.get("refreeze"):
                _refreeze(command, boundaries_manager.refreeze())
            elif isinstance(first, str):
                _canvas(command, canvas(boundaries_manager.scan(), first))
            else:
                _debt(
                    command,
                    boundaries_manager.report(),
                    exact=bool(options.get("exact")),
                )
        except (
            BaselineError,
            ImportGraphError,
            ImportScanError,
            SyncError,
            UnknownAppError,
        ) as exc:
            raise CommandError(str(exc)) from exc


def _refuse(options: dict[str, object], *, syncing: bool) -> None:
    """Refuse the options that do not go together, before any scan."""
    first, second = options.get("app"), options.get("sync_app")
    if second is not None and not syncing:
        msg = "Only sync takes a second app."
        raise CommandError(msg)
    if options.get("add") and not syncing:
        msg = "--add only goes with sync."
        raise CommandError(msg)
    if options.get("refreeze") and first is not None:
        msg = "--refreeze records the whole project and takes no app."
        raise CommandError(msg)
    if options.get("exact") and first is not None:
        msg = "--exact compares the whole project and takes no app."
        raise CommandError(msg)


def _debt(command: BaseCommand, report: Report, *, exact: bool) -> None:
    """Write the broken declarations, the findings per app and code, then the totals.

    A broken scan or record stops it first, so the scan is read only once it is whole.
    """
    for finding in report.findings:
        if finding.code in _BROKEN:
            raise CommandError(finding.message)
    scan = boundaries_manager.scan()
    _declarations(command, scan.declarations.findings)
    totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for debt in report.debts:
        row = totals[debt.app or "-", debt.code]
        row[0] += debt.found
        row[1] += debt.recorded
    if totals:
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
    else:
        command.stdout.write(f"No boundary findings. Mode: {report.mode}.")
    for kernel in kernels(scan.project, scan.graph):
        command.stdout.write(
            f"Shared kernel {kernel.label}: {_count(kernel.modules, 'module')}, "
            f"{_count(kernel.lines, 'line')}, used by {_count(kernel.users, 'app')}. "
            "Keep it small, and change it with the consent of the apps that use it."
        )
    if exact:
        _exact(command, scan)


def _declarations(command: BaseCommand, findings: tuple[Finding, ...]) -> None:
    """Write each broken declaration and each app the settings miss, as check fails."""
    if not findings:
        return
    command.stdout.write(command.style.MIGRATE_HEADING("Declarations"))
    for finding in findings:
        command.stdout.write(f"  {finding.where} {finding.code} {finding.message}")
    command.stdout.write("")


def _count(number: int, noun: str) -> str:
    """Spell a count with its noun, singular for one."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _exact(command: BaseCommand, scan: Scan) -> None:
    """Write each `depends_on` entry no edge uses, failing when there is any."""
    unused = unused_declarations(scan.graph, scan.declarations)
    command.stdout.write("")
    if not unused.declaring:
        command.stdout.write(
            "No app declares depends_on, so there is nothing to compare."
        )
        return
    if not unused.entries:
        command.stdout.write("Every depends_on entry is used.")
        return
    command.stdout.write(command.style.MIGRATE_HEADING("Unused declarations"))
    for label, name in unused.entries:
        command.stdout.write(f"  {label} declares {name}, and no edge leads there.")
    msg = f"Entries of depends_on no edge uses: {len(unused.entries)}."
    raise CommandError(msg)


def _sync(command: BaseCommand, scan: Scan, key: str | None, *, add: bool) -> None:
    """Write the diff of the names sync adds, and the files too with `--add`."""
    synced = plan(scan, key)
    project = scan.project
    if not synced.changes or project is None:
        command.stdout.write("Nothing to add.")
    else:
        if add:
            apply(synced.changes, project)
        for change in synced.changes:
            command.stdout.write(change.diff(project), ending="")
        if not add:
            command.stdout.write("Run with --add to write it.")
    if synced.skipped:
        command.stdout.write(
            f"Skipped apps with no AppConfig class of their own in the project: "
            f"{len(synced.skipped)} ({', '.join(synced.skipped)}). Give each an "
            "apps.py with an AppConfig subclass to sync it."
        )


def _section(command: BaseCommand, title: str, lines: list[str]) -> None:
    """Write one canvas section, a dash when it holds nothing."""
    command.stdout.write("")
    command.stdout.write(command.style.MIGRATE_HEADING(title))
    for line in lines or [_NONE]:
        command.stdout.write(line)


def _dependencies(
    found: tuple[Dependency, ...], undeclared: tuple[str, ...] = ()
) -> list[str]:
    """Spell each other app with the count of every edge kind joining them."""
    return [
        f"  {item.app}: "
        + ", ".join(f"{kind} x{count}" for kind, count in item.kinds)
        + (" (undeclared)" if item.app in undeclared else "")
        for item in found
    ]


def _canvas(command: BaseCommand, found: Canvas) -> None:
    """Write the Module Canvas of one app, section by section."""
    base_dir = resolve_base_dir()
    app = found.app
    command.stdout.write(
        command.style.SUCCESS(f"{app.label} ({relative(app.path, base_dir)})")
    )
    if found.isolated:
        command.stdout.write("Isolated: yes")
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
    _section(command, "Depends on", _dependencies(found.depends_on, found.undeclared))
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
