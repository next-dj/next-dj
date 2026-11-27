from collections.abc import Callable
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.pages.loaders import load_page_module, load_python_module_memo
from next.pages.transactions import page_transaction
from next.transactions import AtomicOverride


@pytest.mark.usefixtures("fresh_declarations")
class TestPageTransaction:
    def test_the_aliases_are_read_from_the_call(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(
            tmp_path, "page.transaction(atomic=True, aliases='all')"
        )
        load_python_module_memo(page_file)
        assert page_transaction(page_file) == AtomicOverride(atomic=True, aliases="all")

    @pytest.mark.parametrize(
        ("declaration", "atomic"),
        [
            pytest.param("page.transaction(atomic=False)", False, id="off"),
            pytest.param("page.transaction(atomic='writes')", "writes", id="writes"),
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
        assert page_transaction(page_file) == AtomicOverride(atomic=atomic)

    def test_an_unusable_value_raises_on_load(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.transaction(atomic='yes')")
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, ImproperlyConfigured)
        assert str(error.__cause__) == (
            f"page.transaction() in {page_file} declares nothing, since atomic must be True, False or 'writes', got 'yes'."
        )
        assert page_transaction(page_file) is None

    def test_a_positional_mode_raises_on_load(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.transaction(True)")
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, TypeError)
        assert str(error.__cause__) == (
            "DeclaringCalls.transaction() takes 1 positional argument but 2 were given"
        )
        assert page_transaction(page_file) is None
