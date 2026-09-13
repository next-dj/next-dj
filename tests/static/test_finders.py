from __future__ import annotations

import os
from contextlib import contextmanager
from io import StringIO
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.contrib.staticfiles.finders import get_finders
from django.core.management import call_command
from django.test import override_settings

from next.conf.signals import settings_reloaded
from next.static import NextStaticFilesFinder
from next.static.discovery import default_stems
from next.static.finders import (
    _MappedSourceStorage,
    _scan_directories,
    _ScanRoots,
    discover_colocated_static_assets,
)
from tests.support import (
    MalformedRootsRouter,
    WatchSourcesCase,
    patched_watch_sources,
    restored_static_registries,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


_TEMPLATE_AND_LAYOUT = WatchSourcesCase(
    templates=("about/template.djx",), layouts=("layout.djx",)
)
_TEMPLATE_ONLY = WatchSourcesCase(templates=("about/template.djx",))
_NOTHING_WATCHED = WatchSourcesCase()
_UNROOTED_TEMPLATE = WatchSourcesCase(rooted=False, templates=("about/template.djx",))


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


@pytest.fixture()
def watched_tree(request: pytest.FixtureRequest, pages_tree: Path) -> Iterator[Path]:
    """Patch the four watch seams from the `WatchSourcesCase` row driving the test."""
    sources: WatchSourcesCase = request.param
    with patched_watch_sources(
        pages=[pages_tree] if sources.rooted else [],
        templates={pages_tree / rel for rel in sources.templates},
        layouts={pages_tree / rel for rel in sources.layouts},
        components={pages_tree / rel for rel in sources.components},
    ):
        yield pages_tree


class TestDiscoverColocatedAssets:
    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_AND_LAYOUT], indirect=["watched_tree"]
    )
    def test_picks_up_template_and_layout_assets(self, watched_tree: Path) -> None:
        mapping = discover_colocated_static_assets()

        assert "next/about.css" in mapping
        assert "next/about.js" in mapping
        assert "next/layout.css" in mapping
        assert "next/layout.js" in mapping
        assert mapping["next/about.css"] == (
            (watched_tree / "about" / "template.css").resolve()
        )

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_symlinked_asset_is_named_by_its_target(self, watched_tree: Path) -> None:
        """Request-time discovery spells the target, so the finder has to agree."""
        target = watched_tree / "shared.css"
        target.write_text("body{}")
        link = watched_tree / "about" / "template.css"
        link.unlink()
        link.symlink_to(target)

        mapping = discover_colocated_static_assets()

        assert mapping["next/about.css"] == target.resolve()

    @pytest.mark.parametrize(
        "watched_tree", [_UNROOTED_TEMPLATE], indirect=["watched_tree"]
    )
    def test_missing_page_root_is_skipped(self, watched_tree: Path) -> None:
        assert (watched_tree / "about" / "template.css").exists()
        assert discover_colocated_static_assets() == {}


class TestNextStaticFilesFinderFind:
    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_find_returns_path_for_known_asset(self, watched_tree: Path) -> None:
        result = NextStaticFilesFinder().find("next/about.css")

        assert result == str((watched_tree / "about" / "template.css").resolve())

    @pytest.mark.parametrize(
        "watched_tree", [_NOTHING_WATCHED], indirect=["watched_tree"]
    )
    def test_find_returns_none_for_unknown_asset(self, watched_tree: Path) -> None:
        assert NextStaticFilesFinder().find("next/missing.css") is None

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_find_all_returns_list(self, watched_tree: Path) -> None:
        found = NextStaticFilesFinder().find("next/about.css", find_all=True)

        assert found == [str((watched_tree / "about" / "template.css").resolve())]

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_find_honours_deprecated_all_keyword(self, watched_tree: Path) -> None:
        found = NextStaticFilesFinder().find("next/about.css", all=True)

        assert found == [str((watched_tree / "about" / "template.css").resolve())]


class TestNextStaticFilesFinderList:
    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_AND_LAYOUT], indirect=["watched_tree"]
    )
    def test_list_yields_all_discovered_assets(self, watched_tree: Path) -> None:
        items = dict(NextStaticFilesFinder().list(ignore_patterns=None))

        assert set(items) == {
            "next/about.css",
            "next/about.js",
            "next/layout.css",
            "next/layout.js",
        }
        storage = items["next/about.css"]
        assert storage.path("next/about.css") == str(
            (watched_tree / "about" / "template.css").resolve()
        )

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_list_respects_ignore_patterns(self, watched_tree: Path) -> None:
        items = dict(NextStaticFilesFinder().list(ignore_patterns=["*.js"]))

        assert set(items) == {"next/about.css"}


class TestScanDirectorySnapshot:
    """What the snapshot records, tree by tree."""

    def test_a_tree_that_does_not_stat_is_recorded_as_none(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "gone"

        assert _scan_directories(_ScanRoots((missing,), ())) == ((missing, None),)

    def test_a_root_that_holds_no_directory_ends_the_walk(self, tmp_path: Path) -> None:
        leaf = tmp_path / "notes.txt"
        leaf.write_text("")

        recorded = _scan_directories(_ScanRoots((), (leaf,)))

        assert [path for path, _ in recorded] == [leaf]

    def test_a_tree_reached_twice_is_descended_once(self, tmp_path: Path) -> None:
        (tmp_path / "inner").mkdir()

        walked = [
            path for path, _ in _scan_directories(_ScanRoots((tmp_path,) * 2, ()))
        ]

        assert walked.count(tmp_path) == 2
        assert walked.count(tmp_path / "inner") == 1

    def test_a_bytecode_cache_and_a_dot_directory_stay_out(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "about").mkdir()

        walked = [path for path, _ in _scan_directories(_ScanRoots((tmp_path,), ()))]

        assert walked == [tmp_path, tmp_path / "about"]


class TestFinderFreshness:
    """The held answer is rebuilt when what it was read from moves, and not before."""

    @contextmanager
    def _counted_scan(self) -> Iterator[mock.MagicMock]:
        with mock.patch(
            "next.static.finders.discover_colocated_static_assets",
            wraps=discover_colocated_static_assets,
        ) as scan:
            yield scan

    @staticmethod
    def _move_mtime(directory: Path) -> None:
        """Push the directory mtime forward, past any filesystem granularity."""
        moved = directory.stat().st_mtime + 10
        os.utime(directory, (moved, moved))

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_an_unmoved_tree_is_walked_once(self, watched_tree: Path) -> None:
        assert watched_tree.exists()
        finder = NextStaticFilesFinder()
        with override_settings(DEBUG=True), self._counted_scan() as scan:
            finder.find("next/about.css")
            finder.find("next/about.js")
            list(finder.list(None))

        assert scan.call_count == 1

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_an_asset_that_appeared_is_found(self, watched_tree: Path) -> None:
        asset = watched_tree / "about" / "template.js"
        asset.unlink()
        with override_settings(DEBUG=True):
            finder = NextStaticFilesFinder()
            assert finder.find("next/about.js") is None

            asset.write_text("//")
            self._move_mtime(asset.parent)

            assert finder.find("next/about.js") == str(asset.resolve())

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_registered_stem_rebuilds_the_answer(self, watched_tree: Path) -> None:
        assert watched_tree.exists()
        finder = NextStaticFilesFinder()
        finder.find("next/about.css")

        with restored_static_registries(), self._counted_scan() as scan:
            default_stems.register("template", "styles")
            finder.find("next/about.css")

        assert scan.call_count == 1

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_reported_page_tree_rebuilds_the_answer(self, watched_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with override_settings(DEBUG=True):
            finder.find("next/about.css")

        with (
            override_settings(DEBUG=True),
            mock.patch(
                "next.static.finders.get_pages_directories_for_watch",
                return_value=[watched_tree, watched_tree / "about"],
            ),
            self._counted_scan() as scan,
        ):
            finder.find("next/about.css")

        assert scan.call_count == 1

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_component_tree_rebuilds_the_answer(self, watched_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        finder.find("next/about.css")

        with (
            mock.patch(
                "next.static.finders.component_watch_roots", return_value=[watched_tree]
            ),
            self._counted_scan() as scan,
        ):
            finder.find("next/about.css")

        assert scan.call_count == 1

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_settings_reload_rebuilds_the_answer(self, watched_tree: Path) -> None:
        assert watched_tree.exists()
        finder = NextStaticFilesFinder()
        finder.find("next/about.css")

        with self._counted_scan() as scan:
            settings_reloaded.send(sender=None)
            finder.find("next/about.css")

        assert scan.call_count == 1

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_process_watching_no_edit_holds_the_answer(
        self, watched_tree: Path
    ) -> None:
        asset = watched_tree / "about" / "template.js"
        asset.unlink()
        with override_settings(DEBUG=False):
            finder = NextStaticFilesFinder()
            assert finder.find("next/about.js") is None

            asset.write_text("//")
            self._move_mtime(asset.parent)

            assert finder.find("next/about.js") is None

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_an_answer_read_without_watching_goes_when_watching_starts(
        self, watched_tree: Path
    ) -> None:
        asset = watched_tree / "about" / "template.js"
        asset.unlink()
        finder = NextStaticFilesFinder()
        with override_settings(DEBUG=False):
            assert finder.find("next/about.js") is None

        asset.write_text("//")
        with override_settings(DEBUG=True):
            assert finder.find("next/about.js") == str(asset)

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_ONLY], indirect=["watched_tree"]
    )
    def test_a_process_watching_no_edit_still_follows_the_trees(
        self, watched_tree: Path
    ) -> None:
        with override_settings(DEBUG=False):
            finder = NextStaticFilesFinder()
            finder.find("next/about.css")

            with (
                mock.patch(
                    "next.static.finders.get_pages_directories_for_watch",
                    return_value=[],
                ),
                self._counted_scan() as scan,
            ):
                assert finder.find("next/about.css") is None

            assert scan.call_count == 1


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


class TestMalformedRouterSurvival:
    """The static paths read routers through the guard, so a wrong shape is inert.

    Nothing about the watch helpers is mocked here, so the real discovery is
    what survives a backend answering bare paths instead of `PageRoot` entries.
    """

    @contextmanager
    def _malformed_router(self, tree: Path) -> Iterator[None]:
        with (
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "broken.Backend"}]}
            ),
            mock.patch(
                "next.urls.RouterFactory.create_backend",
                return_value=MalformedRootsRouter([tree]),
            ),
        ):
            yield

    def test_find_answers_nothing_instead_of_raising(self, pages_tree: Path) -> None:
        with self._malformed_router(pages_tree):
            assert NextStaticFilesFinder().find("next/about.css") is None

    def test_list_answers_nothing_instead_of_raising(self, pages_tree: Path) -> None:
        with self._malformed_router(pages_tree):
            assert list(NextStaticFilesFinder().list(None)) == []

    def test_collectstatic_dry_run_survives(
        self, pages_tree: Path, tmp_path: Path
    ) -> None:
        static_root = tmp_path / "static_root"
        static_root.mkdir()

        with (
            override_settings(STATIC_ROOT=str(static_root)),
            self._malformed_router(pages_tree),
        ):
            out = StringIO()
            call_command(
                "collectstatic", "--noinput", "--dry-run", "--ignore=*.py", stdout=out
            )

        # The run completes, and the malformed backend contributes none of the
        # co-located assets sitting in the tree it failed to report.
        assert "template.css" not in out.getvalue()


class TestCollectstaticIntegration:
    """``collectstatic --dry-run`` enumerates the next-namespace assets."""

    def test_finder_is_registered(self) -> None:
        finders = list(get_finders())
        assert any(isinstance(f, NextStaticFilesFinder) for f in finders)

    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_AND_LAYOUT], indirect=["watched_tree"]
    )
    def test_collectstatic_dry_run_succeeds(
        self, watched_tree: Path, tmp_path: Path
    ) -> None:
        static_root = tmp_path / "static_root"
        static_root.mkdir()

        with override_settings(STATIC_ROOT=str(static_root)):
            out = StringIO()
            call_command(
                "collectstatic", "--noinput", "--dry-run", "--ignore=*.py", stdout=out
            )

        printed = out.getvalue()
        assert "Pretending to copy" in printed
        assert str((watched_tree / "about" / "template.css").resolve()) in printed
