from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from next.pages import transactions as page_transactions
from next.pages.loaders import load_python_module_memo, reset_module_memo
from next.pages.manager import reset_page_registrations
from next.pages.transactions import AtomicDeclaration, atomic_declarations, page_atomic
from next.transactions import AtomicOverride


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    atomic_declarations.reset()
    yield
    atomic_declarations.reset()


def _page(directory: Path, declaration: str) -> Path:
    """Write a `page.py` that runs `declaration` and serves a static template."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        f"from next.pages import page\n\n{declaration}\n\ntemplate = '<p>x</p>'\n"
    )
    return page_file


def _module(file_path: Path, declaration: str) -> Path:
    """Write a module other than a `page.py` that runs `declaration`."""
    file_path.write_text(f"from next.pages import page\n\n{declaration}\n")
    return file_path


class TestPageAtomic:
    def test_a_project_without_a_declaration_reads_nothing(self, tmp_path) -> None:
        with mock.patch.object(page_transactions, "_read") as read:
            assert page_atomic(tmp_path / "page.py") is None
        read.assert_not_called()

    def test_the_call_binds_its_own_page(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic(True, aliases='all')")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=True, aliases="all")

    def test_off_is_an_override_too(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic(False)")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=False)

    def test_another_page_declares_nothing(self, tmp_path) -> None:
        declared = _page(tmp_path / "a", "page.atomic(True)")
        plain = _page(tmp_path / "b", "")
        load_python_module_memo(declared)
        assert page_atomic(plain) is None

    def test_the_last_call_of_a_module_wins(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic(True)\npage.atomic(False)")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=False)

    def test_a_removed_call_stops_applying_after_a_reload(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic(True)")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=True)
        _page(tmp_path, "")
        reset_module_memo()
        assert page_atomic(page_file) is None

    def test_an_unusable_value_declares_nothing(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic('yes')")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) is None

    def test_a_call_inside_a_function_declares_nothing(self, tmp_path) -> None:
        page_file = _page(
            tmp_path, "def declare():\n    page.atomic(True)\n\n\ndeclare()"
        )
        load_python_module_memo(page_file)
        assert page_atomic(page_file) is None
        [(file_path, held)] = list(atomic_declarations.loaded())
        assert file_path == page_file
        assert "inside a function" in str(held.problem())

    def test_the_answer_is_held_until_a_module_loads(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic(True)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_transactions, "_read", wraps=page_transactions._read
        ) as read:
            page_atomic(page_file)
            page_atomic(page_file)
        assert read.call_count == 1

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_read_the_page_every_time(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.atomic(True)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_transactions, "_read", wraps=page_transactions._read
        ) as read:
            assert page_atomic(page_file) == AtomicOverride(atomic=True)
            assert page_atomic(page_file) == AtomicOverride(atomic=True)
        assert read.call_count == 2

    def test_a_page_reset_drops_every_declaration(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path, "page.atomic(True)"))
        load_python_module_memo(_module(tmp_path / "helpers.py", "page.atomic(True)"))
        assert atomic_declarations
        reset_page_registrations()
        assert not atomic_declarations
        assert atomic_declarations.outside() == ()

    @pytest.mark.parametrize("name", ["component.py", "helpers.py"])
    def test_a_call_outside_a_page_module_declares_nothing(
        self, tmp_path, name: str
    ) -> None:
        page_file = _page(tmp_path, "")
        source = _module(tmp_path / name, "page.atomic(True)")
        load_python_module_memo(source)
        load_python_module_memo(page_file)
        assert not atomic_declarations
        assert page_atomic(page_file) is None
        assert page_atomic(source) is None
        assert atomic_declarations.outside() == (source,)


class TestAtomicDeclarations:
    def test_a_module_that_failed_to_load_holds_nothing(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        atomic_declarations.declare(
            page_file, AtomicDeclaration(True, None, {}, top_level=True)
        )
        assert atomic_declarations.current(page_file, None) is None

    def test_a_stale_call_is_skipped(self, tmp_path) -> None:
        page_file = _page(tmp_path, "")
        atomic_declarations.declare(
            page_file, AtomicDeclaration(True, None, {}, top_level=True)
        )
        assert list(atomic_declarations.loaded()) == []

    def test_every_change_moves_the_version(self, tmp_path) -> None:
        start = atomic_declarations.version
        atomic_declarations.declare(
            tmp_path / "page.py", AtomicDeclaration(True, None, {}, top_level=True)
        )
        atomic_declarations.reset()
        assert atomic_declarations.version == start + 2
        assert not atomic_declarations
