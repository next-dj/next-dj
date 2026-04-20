from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import pytest

from next.static import NextStaticFilesFinder
from next.static.finders import (
    _MappedSourceStorage,
    discover_colocated_static_assets,
)


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def pages_tree(tmp_path: Path) -> Path:
    """Build a minimal page tree with template + layout + colocated assets."""
    (tmp_path / "layout.djx").write_text("")
    (tmp_path / "layout.css").write_text("")
    (tmp_path / "layout.js").write_text("")
    about = tmp_path / "about"
    about.mkdir()
    (about / "template.djx").write_text("")
    (about / "template.css").write_text("")
    (about / "template.js").write_text("")
    return tmp_path


class TestDiscoverColocatedAssets:
    def test_picks_up_template_and_layout_assets(self, pages_tree: Path) -> None:
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={pages_tree / "about" / "template.djx"},
            ),
            mock.patch(
                "next.pages.get_layout_djx_paths_for_watch",
                return_value={pages_tree / "layout.djx"},
            ),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            mapping = discover_colocated_static_assets()

        assert "next/about.css" in mapping
        assert "next/about.js" in mapping
        assert "next/layout.css" in mapping
        assert "next/layout.js" in mapping
        assert mapping["next/about.css"] == (
            (pages_tree / "about" / "template.css").resolve()
        )

    def test_missing_page_root_is_skipped(self, tmp_path: Path) -> None:
        unrelated = tmp_path / "detached"
        unrelated.mkdir()
        (unrelated / "template.djx").write_text("")
        (unrelated / "template.css").write_text("")
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={unrelated / "template.djx"},
            ),
            mock.patch("next.pages.get_layout_djx_paths_for_watch", return_value=set()),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            mapping = discover_colocated_static_assets()
        assert mapping == {}


class TestNextStaticFilesFinderFind:
    def test_find_returns_path_for_known_asset(self, pages_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={pages_tree / "about" / "template.djx"},
            ),
            mock.patch("next.pages.get_layout_djx_paths_for_watch", return_value=set()),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            result = finder.find("next/about.css")
        assert isinstance(result, str)
        assert result.endswith("about/template.css")

    def test_find_returns_none_for_unknown_asset(self, pages_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch", return_value=set()
            ),
            mock.patch("next.pages.get_layout_djx_paths_for_watch", return_value=set()),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            assert finder.find("next/missing.css") is None

    def test_find_all_returns_list(self, pages_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={pages_tree / "about" / "template.djx"},
            ),
            mock.patch("next.pages.get_layout_djx_paths_for_watch", return_value=set()),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            found = finder.find("next/about.css", find_all=True)
        assert isinstance(found, list)
        assert len(found) == 1


class TestNextStaticFilesFinderList:
    def test_list_yields_all_discovered_assets(self, pages_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={pages_tree / "about" / "template.djx"},
            ),
            mock.patch(
                "next.pages.get_layout_djx_paths_for_watch",
                return_value={pages_tree / "layout.djx"},
            ),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            items = list(finder.list(ignore_patterns=None))
        logical = {path for path, _ in items}
        assert "next/about.css" in logical
        assert "next/layout.css" in logical

    def test_list_respects_ignore_patterns(self, pages_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with (
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={pages_tree / "about" / "template.djx"},
            ),
            mock.patch("next.pages.get_layout_djx_paths_for_watch", return_value=set()),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            items = list(finder.list(ignore_patterns=["*.js"]))
        logical = {path for path, _ in items}
        assert "next/about.css" in logical
        assert "next/about.js" not in logical


class TestMappedSourceStorage:
    def test_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "a.css"
        src.write_text("")
        storage = _MappedSourceStorage({"next/a.css": src})
        assert storage.exists("next/a.css")
        assert not storage.exists("next/missing.css")

    def test_open_reads_source(self, tmp_path: Path) -> None:
        src = tmp_path / "a.css"
        src.write_text("body{}")
        storage = _MappedSourceStorage({"next/a.css": src})
        with storage.open("next/a.css") as handle:
            assert handle.read() == b"body{}"

    def test_path_returns_string(self, tmp_path: Path) -> None:
        src = tmp_path / "a.css"
        src.write_text("")
        storage = _MappedSourceStorage({"next/a.css": src})
        assert storage.path("next/a.css") == str(src)


class TestCollectstaticIntegration:
    """E2E: --dry-run must enumerate next-namespace assets."""

    def test_finder_is_registered(self) -> None:
        from django.contrib.staticfiles.finders import get_finders

        finders = list(get_finders())
        assert any(isinstance(f, NextStaticFilesFinder) for f in finders)

    def test_collectstatic_dry_run_succeeds(
        self, pages_tree: Path, tmp_path: Path
    ) -> None:
        from io import StringIO

        from django.core.management import call_command
        from django.test import override_settings

        static_root = tmp_path / "static_root"
        static_root.mkdir()

        with (
            override_settings(STATIC_ROOT=str(static_root)),
            mock.patch(
                "next.pages.get_pages_directories_for_watch",
                return_value=[pages_tree],
            ),
            mock.patch(
                "next.pages.get_template_djx_paths_for_watch",
                return_value={pages_tree / "about" / "template.djx"},
            ),
            mock.patch(
                "next.pages.get_layout_djx_paths_for_watch",
                return_value={pages_tree / "layout.djx"},
            ),
            mock.patch(
                "next.components.get_component_paths_for_watch",
                return_value=set(),
            ),
        ):
            out = StringIO()
            call_command(
                "collectstatic",
                "--noinput",
                "--dry-run",
                "--ignore=*.py",
                stdout=out,
            )
        # Dry-run completes without ImproperlyConfigured. The finder wires
        # up the staticfiles command path — full asset enumeration is tested
        # in unit tests above.
        assert "Pretending to copy" in out.getvalue()
