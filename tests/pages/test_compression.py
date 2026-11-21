from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from next.pages import compression as page_compression
from next.pages.compression import (
    CompressDeclaration,
    compress_declarations,
    page_compress,
)
from next.pages.loaders import load_python_module_memo, reset_module_memo
from next.pages.manager import reset_page_registrations


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    compress_declarations.reset()
    yield
    compress_declarations.reset()


def _page(directory: Path, declaration: str) -> Path:
    """Write a `page.py` that runs `declaration` and serves a static template."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        f"from next.pages import page\n\n{declaration}\n\ntemplate = '<p>x</p>'\n"
    )
    return page_file


class TestPageCompress:
    def test_a_project_without_a_declaration_reads_nothing(self, tmp_path) -> None:
        with mock.patch.object(page_compression, "_read") as read:
            assert page_compress(tmp_path / "page.py") is None
        read.assert_not_called()

    @pytest.mark.parametrize("enabled", [False, True])
    def test_the_call_binds_its_own_page(self, tmp_path, enabled) -> None:
        page_file = _page(tmp_path, f"page.compress({enabled})")
        load_python_module_memo(page_file)
        assert page_compress(page_file) is enabled

    def test_another_page_declares_nothing(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path / "a", "page.compress(False)"))
        assert page_compress(_page(tmp_path / "b", "")) is None

    def test_a_removed_call_stops_applying_after_a_reload(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.compress(False)")
        load_python_module_memo(page_file)
        assert page_compress(page_file) is False
        _page(tmp_path, "")
        reset_module_memo()
        assert page_compress(page_file) is None

    def test_a_value_that_is_no_bool_declares_nothing(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.compress(0)")
        load_python_module_memo(page_file)
        assert page_compress(page_file) is None
        [(file_path, held)] = list(compress_declarations.loaded())
        assert file_path == page_file
        assert held.problem() == "enabled is 0, which is no bool"

    def test_a_call_inside_a_function_declares_nothing(self, tmp_path) -> None:
        page_file = _page(
            tmp_path, "def declare():\n    page.compress(False)\n\n\ndeclare()"
        )
        load_python_module_memo(page_file)
        assert page_compress(page_file) is None
        [(_file_path, held)] = list(compress_declarations.loaded())
        assert "inside a function" in str(held.problem())

    def test_the_answer_is_held_until_a_module_loads(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.compress(False)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_compression, "_read", wraps=page_compression._read
        ) as read:
            page_compress(page_file)
            page_compress(page_file)
        assert read.call_count == 1

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_read_the_page_every_time(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.compress(False)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_compression, "_read", wraps=page_compression._read
        ) as read:
            assert page_compress(page_file) is False
            assert page_compress(page_file) is False
        assert read.call_count == 2

    def test_a_page_reset_drops_every_declaration(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path, "page.compress(False)"))
        assert compress_declarations
        reset_page_registrations()
        assert not compress_declarations

    def test_a_call_outside_a_page_module_declares_nothing(self, tmp_path) -> None:
        source = tmp_path / "helpers.py"
        source.write_text("from next.pages import page\n\npage.compress(False)\n")
        load_python_module_memo(source)
        assert not compress_declarations
        assert compress_declarations.outside() == (source,)


class TestCompressDeclaration:
    def test_a_bool_is_the_choice(self) -> None:
        declaration = CompressDeclaration(False, {}, top_level=True)
        assert declaration.problem() is None
        assert declaration.choice() is False
