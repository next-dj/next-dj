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

    @pytest.mark.parametrize(
        ("declaration", "atomic"),
        [
            pytest.param("page.atomic(False)", False, id="off"),
            pytest.param("page.atomic('writes')", "writes", id="writes"),
        ],
    )
    def test_a_value_besides_on_is_an_override(
        self,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
        declaration: str,
        atomic: object,
    ) -> None:
        page_file = page_declaring(tmp_path, declaration)
        load_python_module_memo(page_file)
        assert page_atomic(page_file) == AtomicOverride(atomic=atomic)

    def test_an_unusable_value_declares_nothing(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.atomic('yes')")
        load_python_module_memo(page_file)
        assert page_atomic(page_file) is None
        [(file_path, held)] = list(atomic_store.loaded())
        assert file_path == page_file
        assert held.problem() == "atomic must be True, False or 'writes', got 'yes'"
