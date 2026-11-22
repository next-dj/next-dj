from collections.abc import Callable
from pathlib import Path

import pytest

from next.pages.loaders import load_python_module_memo
from next.pages.router import (
    DEFAULT_OPTIONS,
    RouterDeclaration,
    RouterOptions,
    page_router,
    router_store,
)


@pytest.mark.usefixtures("fresh_declarations")
class TestPageRouter:
    def test_the_options_are_read_from_the_call(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(
            tmp_path, "page.router(prefetch=False, snapshot=False)"
        )
        load_python_module_memo(page_file)
        assert page_router(page_file) == RouterOptions(
            navigate=True, prefetch=False, snapshot=False
        )

    def test_a_value_that_is_no_bool_declares_nothing(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.router(prefetch=0, snapshot='no')")
        load_python_module_memo(page_file)
        assert page_router(page_file) is DEFAULT_OPTIONS
        [(file_path, held)] = list(router_store.loaded())
        assert file_path == page_file
        assert held.problem() == "prefetch=0, snapshot='no' is no bool"


class TestRouterDeclaration:
    def test_bools_are_the_options(self) -> None:
        declaration = RouterDeclaration(False, True, True, {}, top_level=True)
        assert declaration.problem() is None
        assert declaration.options() == RouterOptions(navigate=False)
