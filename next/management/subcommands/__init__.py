"""The subcommands of `manage.py next`, one module each, found by module name."""

import importlib
import pkgutil
from argparse import ArgumentParser
from typing import ClassVar

from django.core.management.base import BaseCommand


class BaseSubcommand:
    """A subcommand of `manage.py next`, which its module exposes as `Subcommand`."""

    help: ClassVar[str] = ""

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the options of the subcommand, none by default."""

    def handle(self, command: BaseCommand, **options: object) -> None:
        """Run the subcommand, writing through the output streams of `command`."""
        raise NotImplementedError


def load_subcommands() -> dict[str, BaseSubcommand]:
    """Return an instance of every subcommand in the package, sorted by name."""
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(__path__)
        if not info.name.startswith("_")
    )
    return {
        name: importlib.import_module(f"{__name__}.{name}").Subcommand()
        for name in names
    }
