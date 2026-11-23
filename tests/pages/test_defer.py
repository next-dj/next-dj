from collections.abc import Callable
from pathlib import Path

import pytest

from next.defer import BoundaryOptions, DeferMode
from next.pages.defer import PageDeferDeclaration, page_defer, page_defer_store
from next.pages.loaders import load_python_module_memo


@pytest.mark.usefixtures("fresh_declarations")
class TestPageDefer:
    def test_the_options_are_read_from_the_call(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.defer(timeout='3s', parallel=False)")
        load_python_module_memo(page_file)
        assert page_defer(page_file) == BoundaryOptions(
            mode=DeferMode.STREAM, timeout=3.0, parallel=False
        )

    def test_an_unusable_value_declares_nothing(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.defer(parallel='yes')")
        load_python_module_memo(page_file)
        assert page_defer(page_file) is None
        [(file_path, held)] = list(page_defer_store.loaded())
        assert file_path == page_file
        assert held.problem() == "parallel must be True or False, got 'str'"


class TestPageDeferDeclaration:
    def test_a_usable_call_has_no_problem(self) -> None:
        declaration = PageDeferDeclaration(None, True, {}, top_level=True)
        assert declaration.problem() is None
        assert declaration.options() == BoundaryOptions(
            mode=DeferMode.STREAM, parallel=True
        )
