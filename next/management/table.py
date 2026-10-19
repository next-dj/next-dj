"""The aligned table that `manage.py routes` and `manage.py permissions` print."""

from collections.abc import Sequence
from pathlib import Path

from django.core.management.base import BaseCommand


def relative(path: Path, base_dir: Path | None) -> Path:
    """Return `path` relative to `base_dir` when it lies below it, else unchanged."""
    if base_dir is not None and path.is_relative_to(base_dir):
        return path.relative_to(base_dir)
    return path


def write_table(
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
