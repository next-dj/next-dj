"""`manage.py next`, the dispatcher of every module in `next.management.subcommands`."""

from argparse import ArgumentParser
from typing import Any, override

from django.core.management.base import BaseCommand

from next.management.subcommands import load_subcommands


class Command(BaseCommand):
    """Run the next.dj subcommand named on the command line."""

    help = "Run a next.dj subcommand. Each one has its own --help."

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Give every subcommand module a subparser named after it."""
        subparsers = parser.add_subparsers(
            title="subcommands", dest="subcommand", metavar="subcommand", required=True
        )
        for name, subcommand in load_subcommands().items():
            subparser = subparsers.add_parser(
                name, help=subcommand.help, description=subcommand.help
            )
            subcommand.add_arguments(subparser)

    @override
    def execute(self, *args: Any, **options: Any) -> str | None:
        """Skip the system checks for a subcommand that reports them itself."""
        subcommand = load_subcommands().get(options.get("subcommand", ""))
        if subcommand is not None and subcommand.skips_checks:
            options["skip_checks"] = True
        return super().execute(*args, **options)

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        """Hand the parsed options to the subcommand the parser picked."""
        load_subcommands()[options["subcommand"]].handle(self, **options)
