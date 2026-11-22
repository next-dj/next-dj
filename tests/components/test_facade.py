from pathlib import Path

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
        assert render_component_error(info, {}, deferred=True) == ""
