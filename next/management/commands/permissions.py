"""`manage.py permissions`, the access rule of every endpoint the project answers."""

from argparse import ArgumentParser
from pathlib import Path
from typing import Any, override

from django.core.management.base import BaseCommand, CommandError

from next.conf import next_framework_settings
from next.management.endpoints import (
    Endpoint,
    ViewEndpoint,
    endpoints,
    resolve_page,
    resolve_view,
    view_endpoints,
)
from next.management.table import relative, write_table
from next.permissions.access import login_middleware
from next.utils import resolve_base_dir


class Command(BaseCommand):
    """Print the rule of each endpoint and where its requirements come from."""

    help = (
        "List the access rule of every page, action and component that declares one "
        "and of every other view, or of what one URL answers with."
    )

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Take an optional URL that narrows the table to one page or view."""
        parser.add_argument(
            "url", nargs="?", help="The path of one page or view, as /notes/1/."
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        """Write one aligned row per endpoint under a header."""
        base_dir = resolve_base_dir()
        middleware = _middleware_name()
        default = _default_name(middleware)
        url = options["url"]
        found: tuple[Endpoint, ...] = ()
        views: tuple[ViewEndpoint, ...] = ()
        if url is None:
            found, views = endpoints(), view_endpoints()
        elif (page_path := resolve_page(url)) is not None:
            found = endpoints(page_path)
        elif (view := resolve_view(url)) is not None:
            views = (view,)
        else:
            msg = f"No page or view answers the URL {url!r}."
            raise CommandError(msg)
        rows = [
            *(_row(endpoint, base_dir, default) for endpoint in found),
            *(_view_row(view, middleware) for view in views),
        ]
        write_table(self, ("Kind", "Endpoint", "Access", "Sources"), rows)


def _middleware_name() -> str:
    """Return the class name of the listed login middleware, the source it closes by."""
    entry = login_middleware()
    return "-" if entry is None else entry.rpartition(".")[2]


def _default_name(middleware: str) -> str:
    """Return what turns the default login on, the setting before the middleware."""
    if next_framework_settings.LOGIN_REQUIRED:
        return "LOGIN_REQUIRED"
    return middleware


def _row(endpoint: Endpoint, base_dir: Path | None, default: str) -> tuple[str, ...]:
    """Return the cells of a page, action or component.

    An action carries its file, since one name may be declared by several pages.
    """
    name = endpoint.name
    if endpoint.kind == "action":
        name = f"{name} ({relative(endpoint.file, base_dir)})"
    sources = ", ".join(str(relative(path, base_dir)) for path in endpoint.sources)
    if sources and endpoint.access == "public":
        sources = f"public ({sources})"
    elif not sources and endpoint.closed:
        sources = default
    return (endpoint.kind, name, endpoint.access, sources or "-")


def _view_row(view: ViewEndpoint, middleware: str) -> tuple[str, ...]:
    """Return the cells of a view, the dotted view standing for the file of a page."""
    if view.deferred is not None:
        sources = f"see the {view.deferred} rows"
    elif view.public:
        sources = f"public ({view.view})"
    elif view.closed:
        sources = middleware
    else:
        sources = "-"
    return ("view", view.route, view.access, sources)
