from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.urls import FileRouterBackend
from tests.benchmarks.factories import build_pages_tree
from tests.support import importable_dir


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _router_for(tree: Path) -> FileRouterBackend:
    return FileRouterBackend(
        app_dirs=False,
        extra_root_paths=[tree],
        skip_dir_names=frozenset(),
        components_folder_name="_components",
    )


def _app_dirs_router() -> FileRouterBackend:
    return FileRouterBackend(
        app_dirs=True,
        extra_root_paths=[],
        skip_dir_names=frozenset(),
        components_folder_name="_components",
    )


def _write_apps(
    root: Path, count: int, depth: int, fanout: int, *, with_pages: int | None = None
) -> list[str]:
    """Write ``count`` importable apps, the first ``with_pages`` carrying page trees."""
    paged = count if with_pages is None else with_pages
    names: list[str] = []
    for index in range(count):
        name = f"bench_app_{index}"
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text("")
        if index < paged:
            pages = root / name / "pages"
            pages.mkdir()
            build_pages_tree(pages, depth, fanout, leaf="page.py")
        names.append(name)
    return names


@pytest.fixture()
def installed_page_apps(tmp_path: Path, settings) -> Iterator[None]:
    """Install two apps of 27 pages each so app resolution has trees to find."""
    names = _write_apps(tmp_path, count=2, depth=3, fanout=3)
    with importable_dir(tmp_path):
        settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
        yield


@pytest.fixture()
def installed_many_apps(tmp_path: Path, settings) -> Iterator[None]:
    """Install 100 apps, two of them with pages, the shape of a large project.

    App resolution is per installed app, so a small `INSTALLED_APPS` hides
    anything quadratic in the number of apps.
    """
    names = _write_apps(tmp_path, count=100, depth=2, fanout=3, with_pages=2)
    with importable_dir(tmp_path):
        settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
        yield


class TestBenchFileRouter:
    """A fresh backend per round, because the second call is a cache copy.

    The tree walk runs once per backend and every later `generate_urls` copies
    `_root_patterns_cache`, so timing a reused backend times a list copy of the
    leaf count instead of the discovery these sizes exist to measure.
    """

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_discover_small(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=3, leaf="page.py")  # 27 leaves
        benchmark(lambda: _router_for(tmp_path).generate_urls())

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_discover_medium(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=5, leaf="page.py")  # 125 leaves
        benchmark(lambda: _router_for(tmp_path).generate_urls())

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_discover_large(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=4, fanout=6, leaf="page.py")  # 1296 leaves
        benchmark(lambda: _router_for(tmp_path).generate_urls())


class TestBenchAppDirsRouter:
    """App resolution walks the app registry, which no root-only router touches."""

    @pytest.mark.benchmark(group="urls.backends")
    def test_app_dirs_build_and_generate(self, installed_page_apps, benchmark) -> None:
        benchmark(lambda: _app_dirs_router().generate_urls())

    @pytest.mark.benchmark(group="urls.backends")
    def test_app_dirs_generate_cached(self, installed_page_apps, benchmark) -> None:
        backend = _app_dirs_router()
        backend.generate_urls()
        benchmark(backend.generate_urls)

    @pytest.mark.benchmark(group="urls.backends")
    def test_app_dirs_page_roots(self, installed_page_apps, benchmark) -> None:
        benchmark(lambda: _app_dirs_router().page_roots())


class TestBenchManyInstalledApps:
    """The page-root listing runs per static lookup and per reloader tick."""

    @pytest.mark.benchmark(group="urls.backends")
    def test_page_roots_over_100_apps(self, installed_many_apps, benchmark) -> None:
        benchmark(lambda: _app_dirs_router().page_roots())

    @pytest.mark.benchmark(group="urls.backends")
    def test_build_and_generate_over_100_apps(
        self, installed_many_apps, benchmark
    ) -> None:
        benchmark(lambda: _app_dirs_router().generate_urls())
