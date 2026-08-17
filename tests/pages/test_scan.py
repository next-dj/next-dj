import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.checks import Error

from next.checks import reset_check_caches
from next.pages.scan import iter_serialized_page_context_keys
from tests.support import MalformedRootsRouter, patch_checks_router_manager


@pytest.fixture(autouse=True)
def _reset_check_caches():
    reset_check_caches()
    yield
    reset_check_caches()


class TestSerializedPageContextKeys:
    """iter_serialized_page_context_keys reports what a page.py declares."""

    def _write_page(self, tmp_path: Path, body: str) -> Path:
        page_file = tmp_path / "page.py"
        page_file.write_text(textwrap.dedent(body))
        return page_file

    def test_no_router_manager_reports_nothing(self) -> None:
        with patch(
            "next.pages.scan.get_router_manager", return_value=(None, [Error("x")])
        ):
            assert list(iter_serialized_page_context_keys()) == []

    def test_keyed_serialized_key_is_reported_once_per_page(self, tmp_path) -> None:
        page_file = self._write_page(
            tmp_path,
            """
            from next.pages import page


            @page.context("unread", serialize=True)
            def unread():
                return 3
            """,
        )
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch(
                "next.checks.common.walk_page_tree",
                return_value=[("first", page_file), ("second", page_file)],
            ),
        ):
            found = list(iter_serialized_page_context_keys())
        assert found == [(page_file, "unread")]

    def test_symlinked_spelling_of_one_page_reports_its_key_once(
        self, tmp_path
    ) -> None:
        real = tmp_path / "real"
        real.mkdir()
        page_file = self._write_page(
            real,
            """
            from next.pages import page


            @page.context("unread", serialize=True)
            def unread():
                return 3
            """,
        )
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        linked = tmp_path / "link" / "page.py"
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch(
                "next.checks.common.walk_page_tree",
                return_value=[("real", page_file), ("link", linked)],
            ),
        ):
            found = list(iter_serialized_page_context_keys())
        assert found == [(page_file, "unread")]

    def test_unserialized_and_keyless_contexts_are_skipped(self, tmp_path) -> None:
        self._write_page(
            tmp_path,
            """
            from next.pages import page


            @page.context("plain")
            def plain():
                return 1


            @page.context(serialize=True)
            def spread() -> dict:
                return {"$dev": True}
            """,
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert list(iter_serialized_page_context_keys()) == []

    def test_virtual_page_path_is_skipped(self, tmp_path) -> None:
        (tmp_path / "virtual").mkdir()
        (tmp_path / "virtual" / "template.djx").write_text("<p>ok</p>\n")

        with patch_checks_router_manager(pages_directory=tmp_path):
            assert list(iter_serialized_page_context_keys()) == []

    def test_unimportable_page_is_skipped(self, tmp_path) -> None:
        self._write_page(tmp_path, "def broken(:\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert list(iter_serialized_page_context_keys()) == []

    def test_a_router_reporting_the_wrong_tree_shape_is_skipped(self, tmp_path) -> None:
        # This runs on the render path, so a plugin handing back bare paths
        # may not turn a page render into an AttributeError.
        manager = MagicMock()
        manager.backends = (MalformedRootsRouter([tmp_path]),)
        with patch("next.pages.scan.get_router_manager", return_value=(manager, [])):
            assert list(iter_serialized_page_context_keys()) == []
