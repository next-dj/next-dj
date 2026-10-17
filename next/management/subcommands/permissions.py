"""`manage.py next permissions`, the access rule of every page, action and component."""

from argparse import ArgumentParser
from typing import override

from django.core.management.base import BaseCommand, CommandError

from next.management.subcommands import (
    ENDPOINT_HEADER,
    BaseSubcommand,
    endpoint_row,
    table,
)
from next.permissions.endpoints import endpoints, resolve_page
from next.utils import resolve_base_dir


class Subcommand(BaseSubcommand):
    """Print the rule of each endpoint and the files its requirements come from."""

    help = (
        "List the access rule of every page, action and component that declares one, "
        "or of one page and what it renders."
    )

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Take an optional URL that narrows the table to one page."""
        parser.add_argument(
            "url", nargs="?", help="The path of one page, as /notes/1/."
        )

    @override
    def handle(self, command: BaseCommand, **options: object) -> None:
        """Write one aligned row per endpoint under a header."""
        url = options.get("url")
        page_path = None
        if isinstance(url, str):
            page_path = resolve_page(url)
            if page_path is None:
                msg = f"No page answers the URL {url!r}."
                raise CommandError(msg)
        base_dir = resolve_base_dir()
        rows = [endpoint_row(endpoint, base_dir) for endpoint in endpoints(page_path)]
        table(command, ENDPOINT_HEADER, rows)
