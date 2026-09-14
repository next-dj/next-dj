from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.static.finders import NextStaticFilesFinder
from tests.support import default_page_router_config


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# Enough page directories that a walk of the tree stands out against the
# per-lookup work, and far fewer than a real site of that size holds.
_PAGES = 60
_SECTION_SIZE = 10
_ASSET = "next/sec0/page0.css"


def _build_tree(root: Path) -> None:
    """Write a page tree of co-located templates under two section levels."""
    (root / "layout.djx").write_text("")
    (root / "layout.css").write_text("body{}")
    for index in range(_PAGES):
        directory = root / f"sec{index // _SECTION_SIZE}" / f"page{index}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "page.py").write_text("")
        (directory / "template.djx").write_text("<p>x</p>")
        (directory / "template.css").write_text("body{}")
        (directory / "template.js").write_text("// js")


@pytest.fixture()
def pages_tree(tmp_path: Path) -> Iterator[Path]:
    """Build the tree and point the framework settings at it.

    Runs under `DEBUG`, where the finder re-asks per asset instead of
    trusting a cached answer.
    """
    root = tmp_path / "pages"
    root.mkdir()
    _build_tree(root)
    with override_settings(
        NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(root)}, DEBUG=True
    ):
        yield root


@pytest.fixture()
def collected_tree(tmp_path: Path) -> Iterator[Path]:
    """Build the same tree as `collectstatic` reads it, with edits unwatched."""
    root = tmp_path / "pages"
    root.mkdir()
    _build_tree(root)
    with override_settings(
        NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(root)}
    ):
        yield root


class TestBenchStaticFilesFinder:
    """What staticfiles pays per referenced asset, warm and cold."""

    @pytest.mark.benchmark(group="static.finders")
    def test_find_held(self, benchmark, pages_tree: Path) -> None:
        assert pages_tree.exists()
        finder = NextStaticFilesFinder()
        finder.find(_ASSET)

        benchmark(lambda: finder.find(_ASSET))

    @pytest.mark.benchmark(group="static.finders")
    def test_list_held(self, benchmark, collected_tree: Path) -> None:
        assert collected_tree.exists()
        finder = NextStaticFilesFinder()
        list(finder.list(None))

        benchmark(lambda: list(finder.list(None)))

    @pytest.mark.benchmark(group="static.finders")
    def test_find_cold(self, benchmark, pages_tree: Path) -> None:
        assert pages_tree.exists()

        benchmark(lambda: NextStaticFilesFinder().find(_ASSET))

    @pytest.mark.benchmark(group="static.finders")
    def test_list_cold(self, benchmark, collected_tree: Path) -> None:
        assert collected_tree.exists()

        benchmark(lambda: list(NextStaticFilesFinder().list(None)))
