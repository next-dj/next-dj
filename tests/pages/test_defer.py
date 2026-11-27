from collections.abc import Callable
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.boundaries.options import BoundaryOptions, DeferMode
from next.pages.defer import page_defer
from next.pages.loaders import load_page_module, load_python_module_memo


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

    def test_an_unusable_value_raises_on_load(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.defer(parallel='yes')")
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, ImproperlyConfigured)
        assert str(error.__cause__) == (
            f"page.defer() in {page_file} declares nothing, since parallel must be True or False, got 'str'."
        )
        assert page_defer(page_file) is None
