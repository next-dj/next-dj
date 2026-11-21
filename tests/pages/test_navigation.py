from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from next.pages import navigation as page_navigation
from next.pages.loaders import load_python_module_memo, reset_module_memo
from next.pages.manager import reset_page_registrations
from next.pages.navigation import (
    DEFAULT_OPTIONS,
    RouterDeclaration,
    RouterOptions,
    page_router,
    router_declarations,
)


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    router_declarations.reset()
    yield
    router_declarations.reset()


def _page(directory: Path, declaration: str) -> Path:
    """Write a `page.py` that runs `declaration` and serves a static template."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        f"from next.pages import page\n\n{declaration}\n\ntemplate = '<p>x</p>'\n"
    )
    return page_file


class TestPageRouter:
    def test_a_project_without_a_declaration_reads_nothing(self, tmp_path) -> None:
        with mock.patch.object(page_navigation, "_read") as read:
            assert page_router(tmp_path / "page.py") is DEFAULT_OPTIONS
        read.assert_not_called()

    def test_the_call_binds_its_own_page(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.router(prefetch=False, snapshot=False)")
        load_python_module_memo(page_file)
        assert page_router(page_file) == RouterOptions(
            navigate=True, prefetch=False, snapshot=False
        )

    def test_another_page_keeps_the_defaults(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path / "a", "page.router(navigate=False)"))
        assert page_router(_page(tmp_path / "b", "")) is DEFAULT_OPTIONS

    def test_a_removed_call_stops_applying_after_a_reload(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.router(navigate=False)")
        load_python_module_memo(page_file)
        assert page_router(page_file).navigate is False
        _page(tmp_path, "")
        reset_module_memo()
        assert page_router(page_file) is DEFAULT_OPTIONS

    def test_a_value_that_is_no_bool_declares_nothing(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.router(prefetch=0, snapshot='no')")
        load_python_module_memo(page_file)
        assert page_router(page_file) is DEFAULT_OPTIONS
        [(file_path, held)] = list(router_declarations.loaded())
        assert file_path == page_file
        assert held.problem() == "prefetch=0, snapshot='no' is no bool"

    def test_a_call_inside_a_function_declares_nothing(self, tmp_path) -> None:
        page_file = _page(
            tmp_path, "def declare():\n    page.router(navigate=False)\n\n\ndeclare()"
        )
        load_python_module_memo(page_file)
        assert page_router(page_file) is DEFAULT_OPTIONS
        [(_file_path, held)] = list(router_declarations.loaded())
        assert "inside a function" in str(held.problem())

    def test_the_answer_is_held_until_a_module_loads(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.router(snapshot=False)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_navigation, "_read", wraps=page_navigation._read
        ) as read:
            page_router(page_file)
            page_router(page_file)
        assert read.call_count == 1

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_read_the_page_every_time(self, tmp_path) -> None:
        page_file = _page(tmp_path, "page.router(snapshot=False)")
        load_python_module_memo(page_file)
        with mock.patch.object(
            page_navigation, "_read", wraps=page_navigation._read
        ) as read:
            assert page_router(page_file).snapshot is False
            assert page_router(page_file).snapshot is False
        assert read.call_count == 2

    def test_a_page_reset_drops_every_declaration(self, tmp_path) -> None:
        load_python_module_memo(_page(tmp_path, "page.router(navigate=False)"))
        assert router_declarations
        reset_page_registrations()
        assert not router_declarations

    def test_a_call_outside_a_page_module_declares_nothing(self, tmp_path) -> None:
        source = tmp_path / "helpers.py"
        source.write_text(
            "from next.pages import page\n\npage.router(navigate=False)\n"
        )
        load_python_module_memo(source)
        assert not router_declarations
        assert router_declarations.outside() == (source,)


class TestRouterDeclaration:
    def test_bools_are_the_options(self) -> None:
        declaration = RouterDeclaration(False, True, True, {}, top_level=True)
        assert declaration.problem() is None
        assert declaration.options() == RouterOptions(navigate=False)
