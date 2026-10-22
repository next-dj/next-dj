from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.intercept import intercepts
from next.urls.urlconf import urlconf_mounts
from tests.benchmarks.factories import build_pages_tree
from tests.support import file_router, importable_dir, write_page
from tests.support.cases.intercepts import zone_body


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import FileRouterBackend


def _router_for(tree: Path) -> FileRouterBackend:
    return file_router(app_dirs=False, dirs=[tree])


def _app_dirs_router() -> FileRouterBackend:
    return file_router(app_dirs=True)


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


_PRIVATE_APPS = ("bench_shop", "bench_wallets", "bench_blog", "bench_plain")


def _write_private_apps(root: Path) -> list[str]:
    """Write apps whose page trees each hold a private `_drafts` tree beside them."""
    for name in _PRIVATE_APPS:
        app = root / name
        app.mkdir(parents=True)
        (app / "__init__.py").write_text("")
        tree = app / "pages"
        tree.mkdir()
        build_pages_tree(tree, depth=3, fanout=3, leaf="page.py")
        drafts = tree / "_drafts"
        drafts.mkdir()
        build_pages_tree(drafts, depth=3, fanout=3, leaf="page.py")
    return list(_PRIVATE_APPS)


@pytest.fixture()
def installed_private_apps(tmp_path: Path, settings) -> Iterator[None]:
    """Install four apps with a private `_drafts` tree beside every route."""
    entries = _write_private_apps(tmp_path)
    with importable_dir(tmp_path):
        settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *entries]
        yield


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

    App resolution is per installed app, so a small `INSTALLED_APPS` hides quadratics.
    """
    names = _write_apps(tmp_path, count=100, depth=2, fanout=3, with_pages=2)
    with importable_dir(tmp_path):
        settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
        yield


_PEEK_BODY = zone_body("peek")
_SECTIONS = 3


def _write_grouped_tree(root: Path, sections: int = _SECTIONS) -> int:
    """Write a tree of route groups and intercepts, returning the pages it routes.

    Each section holds nested groups, a group page, a parameter below a group and one
    intercept of every marker, the `(...)` one over the page at the tree root.
    """
    write_page(root, "photo/[int:pk]")
    routed_pages = 1
    for index in range(sections):
        section = f"s_{index}"
        for trail in (
            f"{section}/(marketing)",
            f"{section}/(marketing)/(deep)/about",
            f"{section}/(shop)/[slug]",
            f"{section}/(shop)/cart",
            f"{section}/feed",
            f"{section}/feed/photo/[int:pk]",
            f"{section}/feed/sub",
        ):
            write_page(root, trail)
            routed_pages += 1
        for trail in (
            f"{section}/feed/(.)photo/[int:pk]",
            f"{section}/feed/sub/(..)photo/[int:pk]",
            f"{section}/feed/sub/deep/(...)photo/[int:pk]",
        ):
            write_page(root, trail, body=_PEEK_BODY)
    return routed_pages


class TestBenchFileRouter:
    """A fresh backend per round, because the second call is a cache copy.

    `generate_urls` copies `_root_patterns_cache` after the first walk.
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


class TestBenchGroupedAndInterceptedTree:
    """Route groups and intercept directories, against the plain trees above."""

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_discover_groups_and_intercepts(
        self, tmp_path: Path, benchmark
    ) -> None:
        pages = _write_grouped_tree(tmp_path)
        patterns = _router_for(tmp_path).generate_urls()
        assert len(patterns) == pages
        assert len(intercepts.all()) == _SECTIONS * 3
        benchmark(lambda: _router_for(tmp_path).generate_urls())

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_discover_groups_only(self, tmp_path: Path, benchmark) -> None:
        for index in range(_SECTIONS):
            write_page(tmp_path, f"s_{index}/(marketing)")
            write_page(tmp_path, f"s_{index}/(marketing)/(deep)/about")
            write_page(tmp_path, f"s_{index}/(shop)/[slug]")
            write_page(tmp_path, f"s_{index}/(shop)/cart")
        patterns = _router_for(tmp_path).generate_urls()
        assert len(patterns) == _SECTIONS * 4
        assert intercepts.all() == ()
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


class TestBenchPrivateFolders:
    """A `_name` folder is skipped, so its subtree costs one directory entry."""

    @pytest.mark.benchmark(group="urls.backends")
    def test_private_folders_build_and_generate(
        self, installed_private_apps, benchmark
    ) -> None:
        benchmark(lambda: _app_dirs_router().generate_urls())

    @pytest.mark.benchmark(group="urls.backends")
    def test_private_folders_page_roots(
        self, installed_private_apps, benchmark
    ) -> None:
        benchmark(lambda: _app_dirs_router().page_roots())


class TestBenchUrlconfMounts:
    """`page_roots` reads the URLconf walk on every call, so a memo hit is hot."""

    @pytest.mark.benchmark(group="urls.backends")
    def test_urlconf_mounts_memo_hit(self, benchmark) -> None:
        urlconf_mounts()
        benchmark(urlconf_mounts)
