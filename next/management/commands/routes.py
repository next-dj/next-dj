"""`manage.py routes`, the table of every page route the router serves."""

from pathlib import Path
from typing import Any, override

from django.core.management.base import BaseCommand

from next.management.endpoints import page_endpoint, served_zones
from next.management.table import relative, write_table
from next.pages.loaders import page_boundaries
from next.urls.routes import collect_routes, with_intercepts
from next.utils import resolve_base_dir


_HEADER = ("URL", "Name", "File", "Access")
_BOUNDARY_HEADER = ("Loading", "Error")
_INTERCEPT_HEADER = ("Intercept",)
_NONE = "-"


def _owner(path: Path | None, base_dir: Path | None) -> str:
    """Return the file that owns a boundary of a page, `-` when none does."""
    return _NONE if path is None else str(relative(path, base_dir))


class Command(BaseCommand):
    """Print the URL, name, file and access of every page route."""

    help = "List every page route with its reverse name, file and access."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        """Write one aligned row per route, in resolution order, under a header.

        The boundary and intercept columns join only when a row has one to show.
        """
        base_dir = resolve_base_dir()
        routes = with_intercepts(collect_routes())
        chains = [page_boundaries(route.file) for route in routes]
        bounded = any(chain.boundaries for chain in chains)
        intercepted = any(route.intercept is not None for route in routes)
        header = (
            *_HEADER,
            *(_BOUNDARY_HEADER if bounded else ()),
            *(_INTERCEPT_HEADER if intercepted else ()),
        )
        rows = [
            (
                route.url,
                _NONE if route.intercept is not None else route.name,
                str(relative(route.file, base_dir)),
                page_endpoint(route).access,
                *(
                    (_owner(chain.loading, base_dir), _owner(chain.error, base_dir))
                    if bounded
                    else ()
                ),
                *((served_zones(route),) if intercepted else ()),
            )
            for route, chain in zip(routes, chains, strict=True)
        ]
        write_table(self, header, rows)
