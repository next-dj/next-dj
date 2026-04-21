"""Benchmarks for ``next.urls.backends.FileRouterBackend``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.urls import FileRouterBackend
from tests.benchmarks.factories import build_pages_tree


if TYPE_CHECKING:
    from pathlib import Path


def _router_for(tree: Path) -> FileRouterBackend:
    return FileRouterBackend(
        app_dirs=False,
        extra_root_paths=[tree],
        skip_dir_names=frozenset(),
        components_folder_name="_components",
    )


class TestBenchFileRouter:
    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_generate_small(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=3, leaf="page.py")  # 27 leaves
        backend = _router_for(tmp_path)
        benchmark(backend.generate_urls)

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_generate_medium(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=5, leaf="page.py")  # 125 leaves
        backend = _router_for(tmp_path)
        benchmark(backend.generate_urls)

    @pytest.mark.benchmark(group="urls.backends")
    def test_filerouter_generate_large(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=4, fanout=6, leaf="page.py")  # 1296 leaves
        backend = _router_for(tmp_path)
        benchmark(backend.generate_urls)
