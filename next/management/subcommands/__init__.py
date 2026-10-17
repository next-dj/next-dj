"""The subcommands of `manage.py next`, one module each, found by module name."""

import importlib
import pkgutil
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from django.core.management.base import BaseCommand

from next.permissions.endpoints import Endpoint


class BaseSubcommand:
    """A subcommand of `manage.py next`, which its module exposes as `Subcommand`.

    One that reports the system checks itself skips them, so its errors cannot stop it.
    """

    help: ClassVar[str] = ""
    skips_checks: ClassVar[bool] = False

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


def relative(path: Path, base_dir: Path | None) -> Path:
    """Return `path` relative to `base_dir` when it lies below it, else unchanged."""
    if base_dir is not None and path.is_relative_to(base_dir):
        return path.relative_to(base_dir)
    return path


ENDPOINT_HEADER: tuple[str, ...] = ("Kind", "Endpoint", "Access", "Sources")


def endpoint_row(endpoint: Endpoint, base_dir: Path | None) -> tuple[str, ...]:
    """Return the kind, name, access and source files of an endpoint as a row.

    A public rule names the file whose `public()` opened it.
    """
    sources = ", ".join(str(relative(path, base_dir)) for path in endpoint.sources)
    if sources and endpoint.access == "public":
        sources = f"public ({sources})"
    return (endpoint.kind, endpoint.name, endpoint.access, sources or "-")


def table(
    command: BaseCommand, header: tuple[str, ...], rows: Sequence[tuple[str, ...]]
) -> None:
    """Write `rows` under `header` in columns as wide as their widest cell."""
    widths = [max(map(len, column)) for column in zip(header, *rows, strict=True)]
    command.stdout.write(command.style.MIGRATE_HEADING(_line(header, widths)))
    for row in rows:
        command.stdout.write(_line(row, widths))


def _line(cells: tuple[str, ...], widths: list[int]) -> str:
    padded = (cell.ljust(width) for cell, width in zip(cells, widths, strict=True))
    return "  ".join(padded).rstrip()
