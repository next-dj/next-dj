from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import UTC
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.staticfiles.finders import AppDirectoriesFinder, get_finders
from django.core.management import call_command
from django.test import override_settings

from next.conf.signals import settings_reloaded
from next.static import (
    AssetDiscovery,
    NextAppDirectoriesFinder,
    NextStaticFilesFinder,
    StaticCollector,
)
from next.static.discovery import default_stems
from next.static.finders import (
    _MappedSourceStorage,
    _runtime_bundle_static_files,
    _scan_directories,
    _ScanRoots,
    discover_colocated_static_assets,
    scan_page_tree_assets,
)
from next.static.runtime import NEXT_JS_STATIC_PATH
from tests.support import (
    NESTED_ROOT_CASES,
    TEMPLATE_AND_COMPONENT_SOURCES,
    TEST_ROOT_KEY,
    MalformedRootsRouter,
    NestedRootCase,
    StaticAssetProvider,
    WatchSourcesCase,
    file_router_config_entry,
    patched_watch_sources,
    restored_static_registries,
)


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from next.static import StaticBackend


_TEMPLATE_AND_LAYOUT = WatchSourcesCase(
    templates=("about/template.djx",), layouts=("layout.djx",)
)
_TEMPLATE_ONLY = WatchSourcesCase(templates=("about/template.djx",))
_NOTHING_WATCHED = WatchSourcesCase()
_UNROOTED_TEMPLATE = WatchSourcesCase(rooted=False, templates=("about/template.djx",))
# Two sources of one component folder, which is what makes the walk skip the second.
_ONE_COMPONENT_FOLDER_TWICE = WatchSourcesCase(
    components=("_components/widget/component.py", "_components/widget/component.djx")
)


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
    """Patch the four watch seams the row names, template-only without one."""
    sources: WatchSourcesCase = getattr(request, "param", _TEMPLATE_ONLY)
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

        assert "next/site/about.css" in mapping
        assert "next/site/about.js" in mapping
        assert "next/site/layout.css" in mapping
        assert "next/site/layout.js" in mapping
        assert mapping["next/site/about.css"] == (
            (watched_tree / "about" / "template.css").resolve()
        )

    def test_a_symlinked_asset_is_named_by_its_target(self, watched_tree: Path) -> None:
        """Request-time discovery spells the target, so the finder has to agree."""
        target = watched_tree / "shared.css"
        target.write_text("body{}")
        link = watched_tree / "about" / "template.css"
        link.unlink()
        link.symlink_to(target)

        mapping = discover_colocated_static_assets()

        assert mapping["next/site/about.css"] == target.resolve()

    @pytest.mark.parametrize(
        "watched_tree", [_UNROOTED_TEMPLATE], indirect=["watched_tree"]
    )
    def test_missing_page_root_is_skipped(self, watched_tree: Path) -> None:
        assert (watched_tree / "about" / "template.css").exists()
        assert discover_colocated_static_assets() == {}

    @pytest.mark.parametrize(
        "watched_tree", [TEMPLATE_AND_COMPONENT_SOURCES], indirect=["watched_tree"]
    )
    def test_a_component_folder_contributes_its_colocated_assets(
        self, watched_tree: Path
    ) -> None:
        """The logical name is the folder, while the probed stem stays `component`."""
        mapping = discover_colocated_static_assets()

        assert mapping["next/components/widget.css"] == (
            (watched_tree / "_components" / "widget" / "component.css").resolve()
        )
        assert "next/site/about.css" in mapping

    @pytest.mark.parametrize(
        "watched_tree", [_ONE_COMPONENT_FOLDER_TWICE], indirect=["watched_tree"]
    )
    def test_a_component_folder_named_twice_is_scanned_once(
        self, watched_tree: Path
    ) -> None:
        """Two sources of one folder answer one logical name, not a duplicate scan."""
        mapping = discover_colocated_static_assets()

        assert [name for name in mapping if name.startswith("next/components/")] == [
            "next/components/widget.css"
        ]
        assert mapping["next/components/widget.css"] == (
            (watched_tree / "_components" / "widget" / "component.css").resolve()
        )


class TestNextStaticFilesFinderFind:
    def test_find_returns_path_for_known_asset(self, watched_tree: Path) -> None:
        result = NextStaticFilesFinder().find("next/site/about.css")

        assert result == str((watched_tree / "about" / "template.css").resolve())

    @pytest.mark.parametrize(
        "watched_tree", [_NOTHING_WATCHED], indirect=["watched_tree"]
    )
    def test_find_answers_an_empty_list_for_an_unknown_asset(
        self, watched_tree: Path
    ) -> None:
        assert NextStaticFilesFinder().find("next/missing.css") == []

    def test_find_all_returns_list(self, watched_tree: Path) -> None:
        found = NextStaticFilesFinder().find("next/site/about.css", find_all=True)

        assert found == [str((watched_tree / "about" / "template.css").resolve())]

    def test_find_honours_deprecated_all_keyword(self, watched_tree: Path) -> None:
        found = NextStaticFilesFinder().find("next/site/about.css", all=True)

        assert found == [str((watched_tree / "about" / "template.css").resolve())]


class TestNextStaticFilesFinderList:
    @pytest.mark.parametrize(
        "watched_tree", [_TEMPLATE_AND_LAYOUT], indirect=["watched_tree"]
    )
    def test_list_yields_all_discovered_assets(self, watched_tree: Path) -> None:
        items = dict(NextStaticFilesFinder().list(ignore_patterns=None))

        assert set(items) == {
            "next/site/about.css",
            "next/site/about.js",
            "next/site/layout.css",
            "next/site/layout.js",
            *_runtime_bundle_static_files(),
        }
        storage = items["next/site/about.css"]
        assert storage.path("next/site/about.css") == str(
            (watched_tree / "about" / "template.css").resolve()
        )

    def test_list_respects_ignore_patterns(self, watched_tree: Path) -> None:
        items = dict(NextStaticFilesFinder().list(ignore_patterns=["*.js"]))

        assert set(items) == {
            "next/site/about.css",
            *(
                path
                for path in _runtime_bundle_static_files()
                if not path.endswith(".js")
            ),
        }


class TestScanDirectorySnapshot:
    """What the snapshot records, tree by tree."""

    def test_a_tree_that_does_not_stat_is_recorded_as_none(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "gone"

        assert _scan_directories(_ScanRoots(((missing, None),), ())) == (
            (missing, None),
        )

    def test_a_root_that_holds_no_directory_ends_the_walk(self, tmp_path: Path) -> None:
        leaf = tmp_path / "notes.txt"
        leaf.write_text("")

        recorded = _scan_directories(_ScanRoots((), (leaf,)))

        assert [path for path, _ in recorded] == [leaf]

    def test_a_tree_reached_twice_is_recorded_once(self, tmp_path: Path) -> None:
        """A page tree a components `DIRS` names again is no reason to stat twice."""
        (tmp_path / "inner").mkdir()

        walked = [
            path
            for path, _ in _scan_directories(
                _ScanRoots(((tmp_path, None),), (tmp_path,))
            )
        ]

        assert walked.count(tmp_path) == 1
        assert walked.count(tmp_path / "inner") == 1

    def test_a_tree_that_never_stats_is_recorded_once(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone"

        recorded = _scan_directories(_ScanRoots(((missing, None),), (missing,)))

        assert recorded == ((missing, None),)

    def test_a_symlinked_tree_is_recorded_but_descended_once(
        self, tmp_path: Path
    ) -> None:
        """Two spellings of one tree are two snapshot entries and one walk."""
        real = tmp_path / "real"
        real.mkdir()
        (real / "inner").mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)

        walked = [
            path for path, _ in _scan_directories(_ScanRoots(((real, None),), (link,)))
        ]

        assert walked == [link, link / "inner", real]

    def test_a_bytecode_cache_and_a_dot_directory_stay_out(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "about").mkdir()

        walked = [
            path for path, _ in _scan_directories(_ScanRoots(((tmp_path, None),), ()))
        ]

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

    @pytest.mark.usefixtures("watched_tree")
    def test_an_unmoved_tree_is_walked_once(self) -> None:
        finder = NextStaticFilesFinder()
        with override_settings(DEBUG=True), self._counted_scan() as scan:
            finder.find("next/site/about.css")
            finder.find("next/site/about.js")
            list(finder.list(None))

        assert scan.call_count == 1

    def test_an_asset_that_appeared_is_found(self, watched_tree: Path) -> None:
        asset = watched_tree / "about" / "template.js"
        asset.unlink()
        with override_settings(DEBUG=True):
            finder = NextStaticFilesFinder()
            assert finder.find("next/site/about.js") == []

            asset.write_text("//")
            self._move_mtime(asset.parent)

            assert finder.find("next/site/about.js") == str(asset.resolve())

    @pytest.mark.usefixtures("watched_tree")
    def test_a_registered_stem_rebuilds_the_answer(self) -> None:
        finder = NextStaticFilesFinder()
        finder.find("next/site/about.css")

        with restored_static_registries(), self._counted_scan() as scan:
            default_stems.register("template", "styles")
            finder.find("next/site/about.css")

        assert scan.call_count == 1

    def test_a_reported_page_tree_rebuilds_the_answer(self, watched_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        with override_settings(DEBUG=True):
            finder.find("next/site/about.css")

        with (
            override_settings(DEBUG=True),
            mock.patch(
                "next.static.finders.get_page_root_keys_for_watch",
                return_value=[
                    (watched_tree, TEST_ROOT_KEY),
                    (watched_tree / "about", TEST_ROOT_KEY),
                ],
            ),
            self._counted_scan() as scan,
        ):
            finder.find("next/site/about.css")

        assert scan.call_count == 1

    def test_a_component_tree_rebuilds_the_answer(self, watched_tree: Path) -> None:
        finder = NextStaticFilesFinder()
        finder.find("next/site/about.css")

        with (
            mock.patch(
                "next.static.finders.component_watch_roots", return_value=[watched_tree]
            ),
            self._counted_scan() as scan,
        ):
            finder.find("next/site/about.css")

        assert scan.call_count == 1

    @pytest.mark.usefixtures("watched_tree")
    def test_a_settings_reload_rebuilds_the_answer(self) -> None:
        finder = NextStaticFilesFinder()
        finder.find("next/site/about.css")

        with self._counted_scan() as scan:
            settings_reloaded.send(sender=None)
            finder.find("next/site/about.css")

        assert scan.call_count == 1

    def test_a_process_watching_no_edit_holds_the_answer(
        self, watched_tree: Path
    ) -> None:
        asset = watched_tree / "about" / "template.js"
        asset.unlink()
        with override_settings(DEBUG=False):
            finder = NextStaticFilesFinder()
            assert finder.find("next/site/about.js") == []

            asset.write_text("//")
            self._move_mtime(asset.parent)

            assert finder.find("next/site/about.js") == []

    def test_an_answer_read_without_watching_goes_when_watching_starts(
        self, watched_tree: Path
    ) -> None:
        asset = watched_tree / "about" / "template.js"
        asset.unlink()
        finder = NextStaticFilesFinder()
        with override_settings(DEBUG=False):
            assert finder.find("next/site/about.js") == []

        asset.write_text("//")
        with override_settings(DEBUG=True):
            assert finder.find("next/site/about.js") == str(asset)

    def test_a_process_watching_no_edit_still_follows_the_trees(
        self, watched_tree: Path
    ) -> None:
        with override_settings(DEBUG=False):
            finder = NextStaticFilesFinder()
            finder.find("next/site/about.css")

            with (
                mock.patch(
                    "next.static.finders.get_page_root_keys_for_watch", return_value=[]
                ),
                self._counted_scan() as scan,
            ):
                assert finder.find("next/site/about.css") == []

            assert scan.call_count == 1


class TestMappedSourceStorage:
    def test_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "a.css"
        src.write_text("")
        storage = _MappedSourceStorage({"next/a.css": src, "next/dir.css": tmp_path})
        assert storage.exists("next/a.css")
        assert not storage.exists("next/missing.css")
        assert not storage.exists("next/dir.css")

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

    def test_an_unmapped_name_is_a_missing_file(self, tmp_path: Path) -> None:
        storage = _MappedSourceStorage({})
        with pytest.raises(FileNotFoundError):
            storage.path("next/a.css")

    def test_size_reports_the_source_bytes(self, tmp_path: Path) -> None:
        src = tmp_path / "a.css"
        src.write_text("body{}")
        storage = _MappedSourceStorage({"next/a.css": src})
        assert storage.size("next/a.css") == len("body{}")

    def test_modified_time_is_aware_under_use_tz(self, tmp_path: Path) -> None:
        """`collectstatic` compares this against what the target storage answers."""
        src = tmp_path / "a.css"
        src.write_text("")
        storage = _MappedSourceStorage({"next/a.css": src})

        with override_settings(USE_TZ=True):
            stamped = storage.get_modified_time("next/a.css")

        assert stamped.tzinfo is UTC
        assert stamped.timestamp() == pytest.approx(src.stat().st_mtime)

    def test_modified_time_is_naive_without_use_tz(self, tmp_path: Path) -> None:
        src = tmp_path / "a.css"
        src.write_text("")
        storage = _MappedSourceStorage({"next/a.css": src})

        with override_settings(USE_TZ=False):
            stamped = storage.get_modified_time("next/a.css")

        assert stamped.tzinfo is None


class TestMalformedRouterSurvival:
    """The static paths read routers through the guard, so a wrong shape is inert.

    Real discovery runs against a backend returning bare paths instead of `PageRoot`.
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
            assert NextStaticFilesFinder().find("next/site/about.css") == []

    def test_list_answers_nothing_instead_of_raising(self, pages_tree: Path) -> None:
        with self._malformed_router(pages_tree):
            listed = [path for path, _ in NextStaticFilesFinder().list(None)]

        assert listed == sorted(_runtime_bundle_static_files())

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

        # The run completes and the malformed backend contributes no co-located assets.
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

    @pytest.mark.usefixtures("watched_tree")
    def test_a_second_run_skips_an_unmodified_asset(self, tmp_path: Path) -> None:
        """The source storage answers a modified time, so nothing is recopied."""
        static_root = tmp_path / "static_root"
        static_root.mkdir()

        with override_settings(STATIC_ROOT=str(static_root)):
            call_command(
                "collectstatic", "--noinput", "--ignore=*.py", stdout=StringIO()
            )
            out = StringIO()
            call_command(
                "collectstatic", "--noinput", "--ignore=*.py", verbosity=2, stdout=out
            )

        assert "Skipping 'next/site/about.css' (not modified)" in out.getvalue()


_ADMIN_APP = "django.contrib.admin.apps.SimpleAdminConfig"
_FRAMEWORK_APP = "next"


def _bundle_root(tmp_path: Path, *names: str) -> Path:
    """Build a runtime-bundle root holding the named build outputs and no others."""
    (tmp_path / "next").mkdir()
    for name in names:
        (tmp_path / "next" / name).write_text("")
    return tmp_path


def _python_paths(paths: Iterable[str]) -> list[str]:
    """Return the published paths that name a Python module or its bytecode cache."""
    return [
        path
        for path in paths
        if path.endswith((".py", ".pyc")) or "__pycache__" in Path(path).parts
    ]


class TestRuntimeBundleMapping:
    """What the built client runtime contributes to the finder's mapping."""

    def test_a_built_bundle_maps_the_script_and_its_sourcemap(
        self, tmp_path: Path
    ) -> None:
        root = _bundle_root(tmp_path, "next.min.js", "next.min.js.map")

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            mapping = _runtime_bundle_static_files()

        assert mapping == {
            "next/next.min.js": root / "next" / "next.min.js",
            "next/next.min.js.map": root / "next" / "next.min.js.map",
        }

    def test_every_built_chunk_maps_with_its_sourcemap(self, tmp_path: Path) -> None:
        names = ("next.scripts.min.js", "next.dev.min.js")
        root = _bundle_root(tmp_path, *names, *(f"{name}.map" for name in names))

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            mapping = _runtime_bundle_static_files()

        assert sorted(mapping) == [
            "next/next.dev.min.js",
            "next/next.dev.min.js.map",
            "next/next.scripts.min.js",
            "next/next.scripts.min.js.map",
        ]

    def test_a_missing_sourcemap_is_left_out_of_the_mapping(
        self, tmp_path: Path
    ) -> None:
        """A listed path with no file behind it makes `collectstatic` raise."""
        root = _bundle_root(tmp_path, "next.min.js")

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            mapping = _runtime_bundle_static_files()

        assert mapping == {"next/next.min.js": root / "next" / "next.min.js"}

    def test_a_source_checkout_without_a_build_maps_nothing(
        self, tmp_path: Path
    ) -> None:
        root = _bundle_root(tmp_path)

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            assert _runtime_bundle_static_files() == {}

    def test_an_install_without_the_bundle_folder_maps_nothing(
        self, tmp_path: Path
    ) -> None:
        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", tmp_path):
            assert _runtime_bundle_static_files() == {}

    def test_a_folder_named_like_a_bundle_is_left_out(self, tmp_path: Path) -> None:
        root = _bundle_root(tmp_path, "next.min.js")
        (root / "next" / "next.dev.min.js").mkdir()

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            mapping = _runtime_bundle_static_files()

        assert mapping == {"next/next.min.js": root / "next" / "next.min.js"}

    def test_a_colocated_asset_wins_a_collision_with_a_bundle_path(
        self, tmp_path: Path, watched_tree: Path
    ) -> None:
        colocated = watched_tree / "about" / "template.js"
        root = _bundle_root(tmp_path, "next.min.js")

        with (
            mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root),
            mock.patch(
                "next.static.finders.discover_colocated_static_assets",
                return_value={NEXT_JS_STATIC_PATH: colocated},
            ),
        ):
            found = NextStaticFilesFinder().find(NEXT_JS_STATIC_PATH)

        assert found == str(colocated)


class TestRuntimeBundleIsStattedLive:
    """The bundle is a fixed pair of paths, so it is read per lookup, not per scan."""

    @pytest.mark.parametrize("debug", [True, False], ids=["debug", "production"])
    def test_a_bundle_built_after_the_first_lookup_needs_no_restart(
        self, tmp_path: Path, watched_tree: Path, *, debug: bool
    ) -> None:
        root = _bundle_root(tmp_path)
        finder = NextStaticFilesFinder()

        with (
            override_settings(DEBUG=debug),
            mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root),
        ):
            missing = finder.find(NEXT_JS_STATIC_PATH)
            (root / NEXT_JS_STATIC_PATH).write_text("globalThis.next = {}")
            built = finder.find(NEXT_JS_STATIC_PATH)

        assert missing == []
        assert built == str(root / NEXT_JS_STATIC_PATH)

    def test_a_path_outside_the_bundle_pair_is_never_found(
        self, tmp_path: Path, watched_tree: Path
    ) -> None:
        root = _bundle_root(tmp_path)
        (root / "next" / "stray.js").write_text("")

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            assert NextStaticFilesFinder().find("next/stray.js") == []

    def test_a_bundle_built_after_the_first_listing_is_listed_at_once(
        self, tmp_path: Path, watched_tree: Path
    ) -> None:
        root = _bundle_root(tmp_path)
        finder = NextStaticFilesFinder()

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            before = dict(finder.list(ignore_patterns=None))
            (root / NEXT_JS_STATIC_PATH).write_text("globalThis.next = {}")
            after = dict(finder.list(ignore_patterns=None))

        assert NEXT_JS_STATIC_PATH not in before
        assert NEXT_JS_STATIC_PATH in after
        assert after[NEXT_JS_STATIC_PATH].path(NEXT_JS_STATIC_PATH) == str(
            root / NEXT_JS_STATIC_PATH
        )

    def test_a_bundle_deleted_after_a_listing_stops_being_listed(
        self, tmp_path: Path, watched_tree: Path
    ) -> None:
        root = _bundle_root(tmp_path)
        (root / NEXT_JS_STATIC_PATH).write_text("globalThis.next = {}")
        finder = NextStaticFilesFinder()

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            before = dict(finder.list(ignore_patterns=None))
            (root / NEXT_JS_STATIC_PATH).unlink()
            after = dict(finder.list(ignore_patterns=None))
            missing = finder.find(NEXT_JS_STATIC_PATH)

        assert NEXT_JS_STATIC_PATH in before
        assert NEXT_JS_STATIC_PATH not in after
        assert missing == []

    def test_the_discovered_assets_are_listed_ahead_of_the_bundle(
        self, tmp_path: Path, watched_tree: Path
    ) -> None:
        root = _bundle_root(tmp_path)
        (root / NEXT_JS_STATIC_PATH).write_text("globalThis.next = {}")

        with mock.patch("next.static.finders._RUNTIME_BUNDLE_ROOT", root):
            listed = [path for path, _ in NextStaticFilesFinder().list(None)]

        assert listed == [
            "next/site/about.css",
            "next/site/about.js",
            NEXT_JS_STATIC_PATH,
        ]


class TestNextAppDirectoriesFinder:
    """The app finder publishes every app static directory but the framework's own."""

    def test_the_framework_app_is_dropped_and_every_other_app_is_kept(self) -> None:
        with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, _ADMIN_APP]):
            stock = AppDirectoriesFinder()
            finder = NextAppDirectoriesFinder()

        assert _FRAMEWORK_APP in stock.apps
        assert finder.apps == [name for name in stock.apps if name != _FRAMEWORK_APP]
        assert set(finder.storages) == set(stock.storages) - {_FRAMEWORK_APP}
        assert "django.contrib.admin" in finder.storages

    def test_the_finder_publishes_none_of_the_package_modules(self) -> None:
        published = [path for path, _ in NextAppDirectoriesFinder().list(None)]

        assert _python_paths(published) == []


class TestNoPythonSourceIsCollected:
    """No configured finder offers `collectstatic` a Python module to copy."""

    def test_no_configured_finder_publishes_a_python_path(self) -> None:
        published = [path for finder in get_finders() for path, _ in finder.list(None)]

        assert _python_paths(published) == []

    def test_the_stock_app_finder_is_the_one_that_would(self) -> None:
        published = [path for path, _ in AppDirectoriesFinder().list(None)]

        assert _python_paths(published) != []


def _write_template(root: Path, rel: str = "about") -> Path:
    """Write a template and its stylesheet under `root` and return the template."""
    directory = root / rel
    directory.mkdir(parents=True)
    (directory / "template.css").write_text("")
    template = directory / "template.djx"
    template.write_text("")
    return template


class TestScanPageTreeAssets:
    @pytest.mark.parametrize("reverse", [False, True], ids=["in_order", "reversed"])
    def test_a_shared_name_goes_to_the_first_sorted_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, *, reverse: bool
    ) -> None:
        first = tmp_path / "a"
        second = tmp_path / "b"
        templates = {_write_template(first), _write_template(second)}
        roots = [first, second][:: -1 if reverse else 1]
        name = "next/site/about.css"

        with (
            patched_watch_sources(pages=roots, templates=templates),
            caplog.at_level(logging.WARNING, logger="next.static.finders"),
        ):
            scan = scan_page_tree_assets()
            mapping = discover_colocated_static_assets()

        kept = first / "about" / "template.css"
        lost = second / "about" / "template.css"
        assert scan.mapping == {name: kept}
        assert scan.collisions == ((name, kept, lost),)
        assert mapping == {name: kept}
        warning = caplog.records[0].getMessage()
        assert [record.levelno for record in caplog.records] == [logging.WARNING] * 2
        assert str(kept) in warning
        assert str(lost) in warning
        assert name in warning

    def test_distinct_names_collide_nowhere(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        about = _write_template(tmp_path, "about")
        blog = _write_template(tmp_path, "blog")

        with (
            patched_watch_sources(pages=[tmp_path], templates={about, blog}),
            caplog.at_level(logging.WARNING, logger="next.static.finders"),
        ):
            scan = scan_page_tree_assets()

        assert set(scan.mapping) == {"next/site/about.css", "next/site/blog.css"}
        assert scan.collisions == ()
        assert caplog.records == []

    def test_a_keyless_root_serves_neither_layout_nor_template(
        self, tmp_path: Path
    ) -> None:
        template = _write_template(tmp_path)
        (tmp_path / "layout.css").write_text("")
        layout = tmp_path / "layout.djx"
        layout.write_text("")

        with patched_watch_sources(
            pages=[(tmp_path, None)], templates={template}, layouts={layout}
        ):
            scan = scan_page_tree_assets()

        assert scan.mapping == {}
        assert scan.collisions == ()

    def test_components_keep_their_name_under_a_keyless_root(
        self, tmp_path: Path
    ) -> None:
        template = _write_template(tmp_path)
        widget = tmp_path / "_components" / "widget" / "component.py"

        with patched_watch_sources(
            pages=[(tmp_path, None)], templates={template}, components={widget}
        ):
            mapping = discover_colocated_static_assets()

        assert mapping == {"next/components/widget.css": widget.with_suffix(".css")}


class TestNestedRootsAgree:
    @staticmethod
    def _tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        outer = tmp_path.resolve()
        inner = outer / "inner"
        template = _write_template(inner)
        layout = inner / "layout.djx"
        layout.write_text("")
        (inner / "layout.css").write_text("")
        page = inner / "about" / "page.djx"
        page.write_text("")
        return outer, inner, template, layout

    @pytest.mark.parametrize("case", NESTED_ROOT_CASES, ids=lambda case: case.id)
    def test_discovery_names_a_page_as_the_finders_serve_it(
        self, tmp_path: Path, file_backend: StaticBackend, case: NestedRootCase
    ) -> None:
        outer, inner, _template, _layout = self._tree(tmp_path)
        roots = ((outer, case.outer_key), (inner, case.inner_key))
        collector = StaticCollector()

        AssetDiscovery(StaticAssetProvider(file_backend, roots)).discover_page_assets(
            inner / "about" / "page.djx", collector
        )

        urls = sorted(asset.url for asset in collector.assets_in_slot("styles"))
        assert urls == [f"/static/{name}" for name in case.expected]

    @pytest.mark.parametrize("case", NESTED_ROOT_CASES, ids=lambda case: case.id)
    def test_finders_serve_the_names_discovery_asks_for(
        self, tmp_path: Path, case: NestedRootCase
    ) -> None:
        outer, inner, template, layout = self._tree(tmp_path)
        roots = [(outer, case.outer_key), (inner, case.inner_key)]

        with patched_watch_sources(pages=roots, templates={template}, layouts={layout}):
            mapping = discover_colocated_static_assets()

        assert sorted(mapping) == list(case.expected)


class TestDirsRootLayoutAgrees:
    """A `DIRS` root layout reached from an app page is named alike in both."""

    def test_discovery_and_finders_name_it_under_its_own_key(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        base = tmp_path.resolve()
        app_pages = base / "blog" / "pages"
        shared = base / "site"
        (app_pages / "about").mkdir(parents=True)
        shared.mkdir()
        (shared / "layout.css").write_text("body{}")
        layout = shared / "layout.djx"
        layout.write_text("")
        page = app_pages / "about" / "page.djx"
        page.write_text("")
        roots = [(app_pages, "blog/pages"), (shared, "site")]
        config = [file_router_config_entry(dirs=[shared])]

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}):
            collector = StaticCollector()
            AssetDiscovery(
                StaticAssetProvider(file_backend, tuple(roots))
            ).discover_page_assets(page, collector)
        with patched_watch_sources(pages=roots, layouts={layout}):
            mapping = discover_colocated_static_assets()

        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/site/layout.css"
        ]
        assert mapping == {"next/site/layout.css": shared / "layout.css"}
