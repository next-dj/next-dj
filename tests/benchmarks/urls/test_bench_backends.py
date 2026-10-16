from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.urls.urlconf import urlconf_mounts
from tests.benchmarks.factories import build_pages_tree
from tests.support import file_router, importable_dir


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


_MOUNTED_APPS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("bench_shop", 'mount = "shop/"', {"pages": "shop/"}),
    (
        "bench_wallets",
        'mounts = {"pages": "wallets/<int:wallet>/", "staff_pages": "staff/"}',
        {"pages": "wallets/<int:wallet>/", "staff_pages": "staff/"},
    ),
    ("bench_blog", 'mount = ""', {"pages": ""}),
    ("bench_plain", "", {"pages": ""}),
)

_APP_CONFIG = """from django.apps import AppConfig


class BenchConfig(AppConfig):
    name = "{name}"
    {declaration}
"""


def _write_mounted_apps(root: Path, *, private: bool) -> list[str]:
    """Write apps declaring `mount` or `mounts`, with `_drafts` trees when `private`."""
    entries: list[str] = []
    for name, declaration, trees in _MOUNTED_APPS:
        app = root / name
        app.mkdir(parents=True)
        (app / "__init__.py").write_text("")
        (app / "apps.py").write_text(
            _APP_CONFIG.format(name=name, declaration=declaration)
        )
        for subdir in trees:
            tree = app / subdir
            tree.mkdir()
            build_pages_tree(tree, depth=3, fanout=3, leaf="page.py")
            if private:
                drafts = tree / "_drafts"
                drafts.mkdir()
                build_pages_tree(drafts, depth=3, fanout=3, leaf="page.py")
        entries.append(f"{name}.apps.BenchConfig")
    return entries


@pytest.fixture()
def installed_mounted_apps(tmp_path: Path, settings) -> Iterator[None]:
    """Install four apps, three of them declaring where their trees mount."""
    entries = _write_mounted_apps(tmp_path, private=False)
    with importable_dir(tmp_path):
        settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *entries]
        yield


@pytest.fixture()
def installed_private_apps(tmp_path: Path, settings) -> Iterator[None]:
    """Install the same apps with a private `_drafts` tree beside every route."""
    entries = _write_mounted_apps(tmp_path, private=True)
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


class TestBenchFileRouter:
    """A fresh backend per round, because the second call is a cache copy.

    `generate_urls` copies `_root_patterns_cache` after the first tree walk,
    so reusing a backend would time a list copy instead of the discovery.
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


class TestBenchMountedApps:
    """Apps declaring `mount` or `mounts` route several trees under their prefixes."""

    @pytest.mark.benchmark(group="urls.backends")
    def test_mounted_apps_build_and_generate(
        self, installed_mounted_apps, benchmark
    ) -> None:
        benchmark(lambda: _app_dirs_router().generate_urls())

    @pytest.mark.benchmark(group="urls.backends")
    def test_mounted_apps_generate_cached(
        self, installed_mounted_apps, benchmark
    ) -> None:
        backend = _app_dirs_router()
        backend.generate_urls()
        benchmark(backend.generate_urls)

    @pytest.mark.benchmark(group="urls.backends")
    def test_mounted_apps_page_roots(self, installed_mounted_apps, benchmark) -> None:
        benchmark(lambda: _app_dirs_router().page_roots())


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
