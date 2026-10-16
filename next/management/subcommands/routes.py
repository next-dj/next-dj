"""`manage.py next routes`, the table of every page route the router serves."""

from pathlib import Path
from typing import override

from django.core.management.base import BaseCommand

from next.management.subcommands import BaseSubcommand
from next.urls.routes import Route, Source, collect_routes
from next.utils import resolve_base_dir


_HEADER = ("URL", "Name", "App", "File", "Source")
_SOURCES: dict[Source, str] = {"root": "root", "mounts": "mounts", "urls": "urls.py"}


class Subcommand(BaseSubcommand):
    """Print the URL, name, app, file and source of every page route."""

    help = "List every page route with its reverse name, app, file and source."

    @override
    def handle(self, command: BaseCommand, **options: object) -> None:
        """Write one aligned row per route, in resolution order, under a header."""
        base_dir = resolve_base_dir()
        rows = [_row(route, base_dir) for route in collect_routes()]
        widths = [max(map(len, column)) for column in zip(_HEADER, *rows, strict=True)]
        command.stdout.write(command.style.MIGRATE_HEADING(_line(_HEADER, widths)))
        for row in rows:
            command.stdout.write(_line(row, widths))


def _row(route: Route, base_dir: Path | None) -> tuple[str, ...]:
    file = route.file
    if base_dir is not None and file.is_relative_to(base_dir):
        file = file.relative_to(base_dir)
    app = route.app or "-"
    return (route.url, route.name, app, str(file), _SOURCES[route.source])


def _line(cells: tuple[str, ...], widths: list[int]) -> str:
    padded = (cell.ljust(width) for cell, width in zip(cells, widths, strict=True))
    return "  ".join(padded).rstrip()
