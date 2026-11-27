from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.pages.declarations import page_declarations
from next.pages.loaders import load_page_module, load_python_module_memo
from next.router.declarations import (
    DEFAULT_OPTIONS,
    RouterOptions,
    page_router,
    router_declaration,
)
from tests.support.pages import write_page


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    page_declarations.reset()
    yield
    page_declarations.reset()


def _page(tmp_path: Path, call: str) -> Path:
    return write_page(tmp_path, source=f"from next.pages import page\n\n{call}\n")


class TestPageRouter:
    def test_it_is_registered_by_ready(self) -> None:
        assert page_declarations.named("router") is router_declaration

    def test_the_options_are_read_from_the_call(self, tmp_path: Path) -> None:
        page_file = _page(tmp_path, "page.router(prefetch=False, snapshot=False)")
        load_python_module_memo(page_file)
        assert page_router(page_file) == RouterOptions(
            navigate=True, prefetch=False, snapshot=False
        )

    def test_an_unusable_value_raises_on_load(self, tmp_path: Path) -> None:
        page_file = _page(tmp_path, "page.router(prefetch=0, snapshot='no')")
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, ImproperlyConfigured)
        assert str(error.__cause__) == (
            f"page.router() in {page_file} declares nothing, since prefetch=0, snapshot='no' is no bool, each flag taking True or False."
        )
        assert page_router(page_file) is DEFAULT_OPTIONS
