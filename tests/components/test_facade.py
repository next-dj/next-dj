from pathlib import Path

import pytest

from next.boundaries.failures import BoundaryFailure, DebugBoundaryFailure, ErrorKind
from next.components import ComponentInfo
from next.components.facade import render_component_error, render_component_loading
from tests.components.sites import ComponentSite


class TestFacade:
    """The boundary files of a component render through its compiled templates."""

    def test_an_unreadable_loading_file_renders_nothing(
        self, site: ComponentSite
    ) -> None:
        info = site.add("card", "C", loading="L")
        assert info.files is not None
        assert info.files.loading is not None
        info.files.loading.unlink()
        assert render_component_loading(info, {}) == ""

    def test_a_component_without_files_renders_no_ui(self, tmp_path: Path) -> None:
        info = ComponentInfo("c", tmp_path, "", None, None, is_simple=False)
        assert render_component_loading(info, {}) == ""
        assert render_component_error(info, {}) == ""
        assert render_component_error(info, {}, deferred=True, wrapped=False) == ""

    @pytest.mark.parametrize(
        ("retry", "button"),
        [
            (True, '<button type="button" data-next-retry>Try again</button>'),
            (False, ""),
        ],
    )
    def test_a_deferred_component_without_files_renders_the_builtin_ui(
        self, tmp_path: Path, *, retry: bool, button: str
    ) -> None:
        info = ComponentInfo("c", tmp_path, "", None, None, is_simple=False)
        error = BoundaryFailure(ErrorKind.ERROR, "d", None, retry)
        assert render_component_error(info, {"error": error}, deferred=True) == (
            '<div role="alert"><p>This part of the page could not be loaded.</p>'
            f"{button}</div>"
        )

    def test_the_builtin_ui_shows_nothing_of_the_failure(self, tmp_path: Path) -> None:
        info = ComponentInfo("c", tmp_path, "", None, None, is_simple=False)
        error = DebugBoundaryFailure(
            ErrorKind.ERROR, "d1g3st", None, False, "secret", "trace"
        )
        html = render_component_error(info, {"error": error}, deferred=True)
        assert html == (
            '<div role="alert"><p>This part of the page could not be loaded.</p></div>'
        )
