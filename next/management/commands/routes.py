"""`manage.py routes`, the table of every page route the router serves."""

from typing import Any, override

from django.core.management.base import BaseCommand

from next.management.endpoints import page_endpoint
from next.management.table import relative, write_table
from next.urls.routes import collect_routes
from next.utils import resolve_base_dir


class Command(BaseCommand):
    """Print the URL, name, file and access of every page route."""

    help = "List every page route with its reverse name, file and access."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        """Write one aligned row per route, in resolution order, under a header."""
        base_dir = resolve_base_dir()
        rows = [
            (
                route.url,
                route.name,
                str(relative(route.file, base_dir)),
                page_endpoint(route).access,
            )
            for route in collect_routes()
        ]
        write_table(self, ("URL", "Name", "File", "Access"), rows)
