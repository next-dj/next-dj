from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from next.defer import BoundaryOptions, DeferMode
from next.pages import deferral as page_deferral
from next.pages.deferral import (
    PageDeferDeclaration,
    page_defer,
    page_defer_declarations,
)
from next.pages.loaders import load_python_module_memo, reset_module_memo
from next.pages.manager import reset_page_registrations


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    page_defer_declarations.reset()
    yield
    page_defer_declarations.reset()


def _page(directory: Path, declaration: str) -> Path:
    """Write a `page.py` that runs `declaration` and serves a static template."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        f"from next.pages import page\n\n{declaration}\n\ntemplate = '<p>x</p>'\n"
    )
    return page_file


class TestPageDefer:
    def test_a_project_without_a_declaration_reads_nothing(self, tmp_path) -> None:
        with mock.patch.object(page_deferral, "_read") as read:
            assert page_defer(tmp_path / "page.py") is None
        read.assert_not_called()

    def test_the_call_binds_its_own_page(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.defer(timeout='3s', parallel=False)")
        load_python_module_memo(page_file)
        assert page_defer(page_file) == BoundaryOptions(
            mode=DeferMode.STREAM, timeout=3.0, parallel=False
        )

    def test_another_page_declares_nothing(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path / "a", "page.defer(timeout=1)"))
        plain = _page(tmp_path / "b", "")
        assert page_defer(plain) is None

    def test_the_last_call_of_a_module_wins(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.defer(timeout=1)\npage.defer(timeout=2)")
        load_python_module_memo(page_file)
        options = page_defer(page_file)
        assert options is not None
        assert options.timeout == 2.0

    def test_a_removed_call_stops_applying_after_a_reload(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.defer(timeout=1)")
        load_python_module_memo(page_file)
        assert page_defer(page_file) is not None
        _page(tmp_path, "")
        reset_module_memo()
        assert page_defer(page_file) is None

    def test_an_unusable_value_declares_nothing(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.defer(parallel='yes')")
        load_python_module_memo(page_file)
        assert page_defer(page_file) is None
        [(file_path, held)] = list(page_defer_declarations.loaded())
        assert file_path == page_file
        assert held.problem() == "parallel must be None, True or False, got 'str'"

    def test_a_call_inside_a_function_declares_nothing(self, tmp_path) -> None:
        page_file = _page(
            tmp_path, "def declare():\n    page.defer(timeout=1)\n\n\ndeclare()"
        )
        load_python_module_memo(page_file)
        assert page_defer(page_file) is None
        [(_file_path, held)] = list(page_defer_declarations.loaded())
        assert "inside a function" in str(held.problem())

    def test_the_answer_is_held_until_a_module_loads(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.defer(timeout=1)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_deferral, "_read", wraps=page_deferral._read
        ) as read:
            page_defer(page_file)
            page_defer(page_file)
        assert read.call_count == 1

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_read_the_page_every_time(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.defer(timeout=1)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_deferral, "_read", wraps=page_deferral._read
        ) as read:
            assert page_defer(page_file) is not None
            assert page_defer(page_file) is not None
        assert read.call_count == 2

    def test_a_page_reset_drops_every_declaration(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path, "page.defer(timeout=1)"))
        helper = tmp_path / "helpers.py"
        helper.write_text("from next.pages import page\n\npage.defer()\n")
        load_python_module_memo(helper)
        assert page_defer_declarations
        reset_page_registrations()
        assert not page_defer_declarations
        assert page_defer_declarations.outside() == ()

    @pytest.mark.parametrize("name", ["component.py", "helpers.py"])
    def test_a_call_outside_a_page_module_declares_nothing(
        self, tmp_path, name: str
    ) -> None:
        source = tmp_path / name
        source.write_text("from next.pages import page\n\npage.defer(timeout=1)\n")
        load_python_module_memo(source)
        assert not page_defer_declarations
        assert page_defer(source) is None
        assert page_defer_declarations.outside() == (source,)


class TestPageDeferDeclaration:
    def test_a_usable_call_has_no_problem(self) -> None:
        declaration = PageDeferDeclaration(None, True, {}, top_level=True)
        assert declaration.problem() is None
        assert declaration.options() == BoundaryOptions(
            mode=DeferMode.STREAM, parallel=True
        )
