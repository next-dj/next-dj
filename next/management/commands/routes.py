"""`manage.py routes`, the table of every page route the router serves."""

from typing import Any, override

from django.core.management.base import BaseCommand

from next.management.endpoints import page_endpoint, served_zones
from next.management.table import relative, write_table
from next.urls.routes import collect_routes, with_intercepts
from next.utils import resolve_base_dir


_HEADER = ("URL", "Name", "File", "Access")


class Command(BaseCommand):
    """Print the URL, name, file and access of every page route."""

    help = "List every page route with its reverse name, file and access."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        """Write one aligned row per route, in resolution order, under a header.

        An `Intercept` column joins only when a row of an intercept page follows.
        """
        base_dir = resolve_base_dir()
        routes = with_intercepts(collect_routes())
        rows = [
            (
                route.url,
                "-" if route.intercept is not None else route.name,
                str(relative(route.file, base_dir)),
                page_endpoint(route).access,
                served_zones(route),
            )
            for route in routes
        ]
        if any(route.intercept is not None for route in routes):
            write_table(self, (*_HEADER, "Intercept"), rows)
        else:
            write_table(self, _HEADER, [row[:-1] for row in rows])
