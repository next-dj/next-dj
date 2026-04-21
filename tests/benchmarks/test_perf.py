"""Micro-benchmarks for FS scan, page render, and autoreload tick.

Benchmarks are skipped by default and opt-in only. Run with:

    uv run pytest tests/benchmarks --benchmark-only --no-cov

The absolute numbers are not meaningful across machines; these tests
are intended to compare against themselves after performance-oriented
refactors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.pages.manager import Page
from next.server.autoreload import NextStatReloader
from next.urls.dispatcher import scan_pages_tree


if TYPE_CHECKING:
    from pathlib import Path


pytest.importorskip("pytest_benchmark")


def _build_tree(root: Path, depth: int, fanout: int, file_per_leaf: str) -> None:
    if depth == 0:
        (root / file_per_leaf).write_text("def render(request):\n    return 'x'\n")
        return
    for i in range(fanout):
        child = root / f"n_{i}"
        child.mkdir()
        _build_tree(child, depth - 1, fanout, file_per_leaf)


@pytest.mark.benchmark(group="fs_scan")
def test_scan_pages_tree_benchmark(tmp_path, benchmark) -> None:
    _build_tree(tmp_path, depth=3, fanout=5, file_per_leaf="page.py")

    def run() -> list[tuple[str, Path]]:
        return list(scan_pages_tree(tmp_path))

    benchmark(run)


@pytest.mark.benchmark(group="render")
def test_page_render_benchmark(tmp_path, benchmark) -> None:
    page_path = tmp_path / "page.py"
    page_path.write_text("def render(request):\n    return 'hello'\n")
    page_obj = Page()
    page_obj.register_template(page_path, "<h1>{{ title }}</h1>")

    def run() -> str:
        return page_obj.render(page_path, title="Bench")

    benchmark(run)


@pytest.mark.benchmark(group="autoreload_tick")
def test_reloader_collect_routes_benchmark(tmp_path, benchmark, monkeypatch) -> None:
    _build_tree(tmp_path, depth=3, fanout=4, file_per_leaf="page.py")
    monkeypatch.setattr(
        "next.server.autoreload.get_pages_directories_for_watch",
        lambda: [tmp_path],
    )
    reloader = NextStatReloader()

    def run() -> int:
        return len(reloader._collect_routes())

    benchmark(run)
