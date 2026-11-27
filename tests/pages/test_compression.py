from collections.abc import Callable
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.pages.compression import page_compress
from next.pages.loaders import load_page_module, load_python_module_memo


@pytest.mark.usefixtures("fresh_declarations")
class TestPageCompress:
    @pytest.mark.parametrize("enabled", [False, True])
    def test_a_bool_is_the_choice(
        self,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
        *,
        enabled: bool,
    ) -> None:
        page_file = page_declaring(tmp_path, f"page.compress(enabled={enabled})")
        load_python_module_memo(page_file)
        assert page_compress(page_file) is enabled

    def test_an_unusable_value_raises_on_load(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.compress(enabled=0)")
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, ImproperlyConfigured)
        assert str(error.__cause__) == (
            f"page.compress() in {page_file} declares nothing, since enabled must be True or False, got 0."
        )
        assert page_compress(page_file) is None

    def test_a_positional_flag_raises_on_load(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.compress(False)")
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, TypeError)
        assert str(error.__cause__) == (
            "DeclaringCalls.compress() takes 1 positional argument but 2 were given"
        )
        assert page_compress(page_file) is None
