from pathlib import Path
from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import override_settings

from next.checks import NEXT, SEO, reset_check_caches
from next.pages.checks.metadata import pages as metadata_pages
from next.pages.checks.metadata.pages import loaded_metadata_pages
from tests.pages.checks.metadata.trees import (
    INHERITED_CALLABLE,
    NAMED_CALLABLE,
    metadata_page,
    scope,
    templated_page,
)
from tests.support import file_router_config_entry, patch_checks_router_manager


class TestLoadedMetadataPages:
    """`loaded_metadata_pages` folds each routed page and carries its own segment."""

    def test_a_broken_chain_folds_to_nothing(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": "Home"}')
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS={"title": "Acme"})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            init_errors, pages = loaded_metadata_pages()
        assert init_errors == []
        assert [entry.static for entry in pages] == [None]
        assert [entry.declared for entry in pages] == [False]
        assert pages[0].segment is not None

    def test_a_callable_marks_the_page_dynamic(self, tmp_path: Path) -> None:
        templated_page(tmp_path, INHERITED_CALLABLE)
        metadata_page(tmp_path / "leaf", '{"title": "Leaf"}')
        templated_page(tmp_path / "named", NAMED_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            _init_errors, pages = loaded_metadata_pages()
        by_trail = {entry.url_path: entry for entry in pages}
        assert by_trail[""].dynamic is True
        assert by_trail["leaf"].dynamic is True
        assert by_trail["named"].dynamic is True
        assert by_trail["named"].raw is None
        assert by_trail["leaf"].static is not None

    def test_a_folded_page_reports_its_chain(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": "Root"}')
        templated_page(tmp_path / "child", "x = 1\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            _init_errors, pages = loaded_metadata_pages()
        by_trail = {entry.url_path: entry for entry in pages}
        assert by_trail["child"].declared is True
        assert by_trail["child"].segment is None
        assert by_trail["child"].static is not None
        assert str(by_trail["child"].static.title) == "Root"


class TestOncePerRun:
    """The routed pages are imported and folded once per check run."""

    def test_a_whole_run_folds_each_page_once(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": "Home"}')
        metadata_page(tmp_path / "about", '{"title": "About"}')
        entry = file_router_config_entry(pages_dir=tmp_path)
        with (
            override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}),
            patch.object(
                metadata_pages, "_metadata_page", wraps=metadata_pages._metadata_page
            ) as fold,
        ):
            run_checks(tags=[NEXT, SEO], include_deployment_checks=True)
        assert fold.call_count == 2

    def test_a_repeat_reuses_the_pass_until_a_reset(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": "Home"}')
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch.object(
                metadata_pages,
                "discover_page_registrations",
                wraps=metadata_pages.discover_page_registrations,
            ) as discover,
        ):
            _errors, first = loaded_metadata_pages()
            assert loaded_metadata_pages()[1] is first
            assert discover.call_count == 1
            reset_check_caches()
            assert loaded_metadata_pages()[1] is not first
            assert discover.call_count == 2

    def test_a_new_router_manager_folds_the_pages_again(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": "Home"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            _errors, first = loaded_metadata_pages()
        with patch_checks_router_manager(pages_directory=tmp_path):
            _errors, second = loaded_metadata_pages()
        assert second is not first
        assert [entry.url_path for entry in second] == [""]

    def test_a_manager_that_fails_to_build_answers_its_errors(self) -> None:
        error = Error("boom", id="next.E007")
        with patch.object(
            metadata_pages, "get_router_manager", return_value=(None, [error])
        ):
            assert loaded_metadata_pages() == ([error], [])
