from django.test import override_settings

from next.pages.ports import PageScanImpl
from next.ports import PageScan
from next.urls.ports import RouterAccessImpl
from tests.support import call_shape, file_router_config_entry, port_methods


class TestPageScanPort:
    """The scan port reaches the pages area without discovery importing it."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert port_methods(PageScan) == ["load_scanned_page_modules"]

    def test_implementation_parameters_match_the_port(self) -> None:
        assert call_shape(PageScanImpl, "load_scanned_page_modules") == call_shape(
            PageScan, "load_scanned_page_modules"
        )

    def test_the_scan_answers_the_routed_pages_of_the_given_manager(
        self, tmp_path
    ) -> None:
        pages = tmp_path / "blog"
        pages.mkdir()
        page_file = pages / "page.py"
        page_file.write_text('template = "ok"\n')
        entry = file_router_config_entry(pages_dir=tmp_path)

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            manager = RouterAccessImpl().create_manager()
            loaded = PageScanImpl().load_scanned_page_modules(manager)

        assert loaded == [("blog", page_file)]
