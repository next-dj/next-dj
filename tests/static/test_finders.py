from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.contrib.staticfiles.finders import get_finders
from django.core.management import call_command
from django.test import override_settings

from next.static import NextStaticFilesFinder
from next.static.finders import _MappedSourceStorage, discover_colocated_static_assets
from tests.support import MalformedRootsRouter, patched_watch_sources


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class _WatchSources:
    """What the reloader reports for one finder run, spelled relative to the tree.

    `rooted` decides whether the tree is reported as a page root at all, which
    is what tells a path under no page tree from one the finder can name.
    """

    rooted: bool = True
    templates: tuple[str, ...] = ()
    layouts: tuple[str, ...] = ()
    components: tuple[str, ...] = ()


_TEMPLATE_AND_LAYOUT = _WatchSources(
    templates=("about/template.djx",), layouts=("layout.djx",)
)
_TEMPLATE_ONLY = _WatchSources(templates=("about/template.djx",))
_NOTHING_WATCHED = _WatchSources()
_UNROOTED_TEMPLATE = _WatchSources(rooted=False, templates=("about/template.djx",))


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
    """Patch the four watch seams from the `_WatchSources` row driving the test."""
    sources: _WatchSources = request.param
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
