from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.pages.watch import get_pages_directories_for_watch
from next.server.autoreload import NextStatReloader, _tree_dir_signature
from tests.benchmarks.factories import build_pages_tree
from tests.support import file_router_config_entry


if TYPE_CHECKING:
    from pathlib import Path


class TestBenchTreeSignature:
    @pytest.mark.parametrize(
        ("depth", "fanout"), [(3, 3), (4, 5)], ids=["small", "large"]
    )
    @pytest.mark.benchmark(group="server.autoreload")
    def test_signature(
        self, tmp_path: Path, depth: int, fanout: int, benchmark
    ) -> None:
        build_pages_tree(tmp_path, depth=depth, fanout=fanout, leaf="page.py")
        benchmark(_tree_dir_signature, tmp_path)


class TestBenchCollectRoutes:
    @pytest.mark.benchmark(group="server.autoreload")
    def test_collect_routes_cached(
        self, tmp_path: Path, monkeypatch, benchmark
    ) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=4, leaf="page.py")
        monkeypatch.setattr(
            "next.server.autoreload.get_pages_directories_for_watch", lambda: [tmp_path]
        )
        reloader = NextStatReloader()
        reloader._collect_routes()  # warm
        benchmark(reloader._collect_routes)

    @pytest.mark.benchmark(group="server.autoreload")
    def test_collect_routes_fresh(self, tmp_path: Path, monkeypatch, benchmark) -> None:
        build_pages_tree(tmp_path, depth=3, fanout=4, leaf="page.py")
        monkeypatch.setattr(
            "next.server.autoreload.get_pages_directories_for_watch", lambda: [tmp_path]
        )

        def run() -> None:
            reloader = NextStatReloader()
            reloader._collect_routes()

        benchmark(run)


class TestBenchWatchDirectories:
    """The reloader asks for the watched page trees once a second."""

    @pytest.mark.benchmark(group="server.autoreload")
    def test_pages_directories_warm(self, tmp_path: Path, benchmark) -> None:
        """The routers are held, so a tick pays the tree probes and nothing else."""
        root = tmp_path / "shell"
        root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [file_router_config_entry(pages_dir=root)]}
        ):
            get_pages_directories_for_watch()
            benchmark(get_pages_directories_for_watch)
