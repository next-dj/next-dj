from collections.abc import Callable
from pathlib import Path

import pytest

from next.pages.loaders import load_python_module_memo
from next.pages.transactions import atomic_store, page_atomic
from next.transactions import AtomicOverride


@pytest.mark.usefixtures("fresh_declarations")
class TestPageAtomic:
    def test_the_aliases_are_read_from_the_call(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.atomic(True, aliases='all')")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=True, aliases="all")

    def test_off_is_an_override_too(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.atomic(False)")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=False)

    def test_writes_waits_for_the_first_write(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.atomic('writes')")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic="writes")

    def test_an_unusable_value_declares_nothing(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.atomic('yes')")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) is None
        [(file_path, held)] = list(atomic_store.loaded())
        assert file_path == page_file
        assert held.problem() == "atomic must be True, False or 'writes', got 'yes'"
