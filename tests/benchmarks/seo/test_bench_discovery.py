from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.seo.discovery import discover_seo_roots, page_tree_roots
from next.urls import router_manager
from tests.support import POSTS_ITEMS, routed, write_tree


if TYPE_CHECKING:
    from pathlib import Path


def _tree(tmp_path: Path) -> Path:
    pages = ("", *(f"section-{n}/page-{m}" for n in range(10) for m in range(10)))
    return write_tree(
        tmp_path / "pages",
        pages=(*pages, "posts/[slug]"),
        sitemap=POSTS_ITEMS,
        robots="",
        robots_txt=b"User-agent: *\n",
    )


class TestBenchDiscovery:
    """Finding and loading the SEO sources of the routed page trees."""

    @pytest.mark.benchmark(group="seo.discovery")
    def test_discover_every_source(self, tmp_path: Path, benchmark) -> None:
        with routed(_tree(tmp_path)):
            assert len(discover_seo_roots(router_manager)) == 1
            benchmark(discover_seo_roots, router_manager)

    @pytest.mark.benchmark(group="seo.discovery")
    def test_memoised_roots(self, tmp_path: Path, benchmark) -> None:
        with routed(_tree(tmp_path)):
            assert page_tree_roots()
            benchmark(page_tree_roots)
