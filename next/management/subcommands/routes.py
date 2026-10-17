"""`manage.py next routes`, the table of every page route the router serves."""

from pathlib import Path
from typing import override

from django.core.management.base import BaseCommand

from next.management.subcommands import BaseSubcommand, relative, table
from next.permissions.endpoints import page_endpoint
from next.urls.routes import Route, Source, collect_routes
from next.utils import resolve_base_dir


_HEADER = ("URL", "Name", "App", "File", "Source", "Access")
_SOURCES: dict[Source, str] = {"root": "root", "mounts": "mounts", "urls": "urls.py"}


class Subcommand(BaseSubcommand):
    """Print the URL, name, app, file, source and access of every page route."""

    help = "List every page route with its reverse name, app, file, source and access."

    @override
    def handle(self, command: BaseCommand, **options: object) -> None:
        """Write one aligned row per route, in resolution order, under a header."""
        base_dir = resolve_base_dir()
        rows = [_row(route, base_dir) for route in collect_routes()]
        table(command, _HEADER, rows)


def _row(route: Route, base_dir: Path | None) -> tuple[str, ...]:
    file = str(relative(route.file, base_dir))
    app = route.app or "-"
    access = page_endpoint(route).access
    return (route.url, route.name, app, file, _SOURCES[route.source], access)
