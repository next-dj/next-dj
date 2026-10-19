from io import StringIO
from pathlib import Path

import pytest
from django.core.management.base import BaseCommand

from next.management.table import relative, write_table


class TestRelative:
    """A path below the base directory is shown from it, any other stays whole."""

    @pytest.mark.parametrize(
        ("path", "base_dir", "shown"),
        [
            (Path("/project/app/page.py"), Path("/project"), Path("app/page.py")),
            (Path("/elsewhere/page.py"), Path("/project"), Path("/elsewhere/page.py")),
            (Path("/project/app/page.py"), None, Path("/project/app/page.py")),
        ],
        ids=["below", "outside", "no_base_dir"],
    )
    def test_relative(self, path: Path, base_dir: Path | None, shown: Path) -> None:
        assert relative(path, base_dir) == shown


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    command = BaseCommand(stdout=StringIO(), no_color=True)
    write_table(command, header, rows)
    return command.stdout.getvalue()


class TestWriteTable:
    """Each column is as wide as its widest cell, and no line ends in padding."""

    def test_columns_align_under_the_header(self) -> None:
        text = _table(("A", "Long head"), [("wide cell", "x"), ("b", "")])

        assert text.splitlines() == ["A          Long head", "wide cell  x", "b"]

    def test_a_header_alone_is_one_line(self) -> None:
        assert _table(("URL", "Name"), []) == "URL  Name\n"

    def test_the_header_is_styled_as_a_heading(self) -> None:
        command = BaseCommand(stdout=StringIO(), force_color=True)

        write_table(command, ("Kind",), [("page",)])

        heading, row = command.stdout.getvalue().splitlines()
        assert heading == command.style.MIGRATE_HEADING("Kind")
        assert heading != "Kind"
        assert row == "page"
