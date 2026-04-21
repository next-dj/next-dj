"""Benchmarks for ``next.server.autoreload``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.server.autoreload import NextStatReloader, _tree_dir_signature
from tests.benchmarks.factories import build_pages_tree


if TYPE_CHECKING:
    from pathlib import Path


class TestBenchTreeSignature:
    @pytest.mark.benchmark(group="server.autoreload")
    def test_signature_small(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=3, leaf="page.py")
        benchmark(_tree_dir_signature, tmp_path)

    @pytest.mark.benchmark(group="server.autoreload")
    def test_signature_large(self, tmp_path: Path, benchmark) -> None:
        build_pages_tree(tmp_path, depth=4, fanout=5, leaf="page.py")
        benchmark(_tree_dir_signature, tmp_path)


class TestBenchCollectRoutes:
    @pytest.mark.benchmark(group="server.autoreload")
    def test_collect_routes_cached(
        self, tmp_path: Path, monkeypatch, benchmark
    ) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=4, leaf="page.py")
        monkeypatch.setattr(
            "next.server.autoreload.get_pages_directories_for_watch",
            lambda: [tmp_path],
        )
        reloader = NextStatReloader()
        reloader._collect_routes()  # warm
        benchmark(reloader._collect_routes)

    @pytest.mark.benchmark(group="server.autoreload")
    def test_collect_routes_fresh(self, tmp_path: Path, monkeypatch, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=4, leaf="page.py")
        monkeypatch.setattr(
            "next.server.autoreload.get_pages_directories_for_watch",
            lambda: [tmp_path],
        )

        def run() -> None:
            reloader = NextStatReloader()
            reloader._collect_routes()

        benchmark(run)
