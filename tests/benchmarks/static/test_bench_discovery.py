from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.static.discovery import PathResolver


if TYPE_CHECKING:
    from pathlib import Path


class TestBenchPathResolver:
    @pytest.mark.benchmark(group="static.discovery")
    def test_find_page_root_hit_cached(self, tmp_path: Path, benchmark) -> None:
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "a").mkdir()
        page_file = tmp_path / "pages" / "a" / "page.py"
        page_file.write_text("# noop")
        roots = (tmp_path / "pages",)
        resolver = PathResolver(lambda: roots)
        resolver.find_page_root(page_file)  # warm
        benchmark(resolver.find_page_root, page_file)

    @pytest.mark.benchmark(group="static.discovery")
    def test_logical_name_for_template_deep(self, tmp_path: Path, benchmark) -> None:
        root = tmp_path / "pages"
        root.mkdir()
        template_dir = root / "a" / "b" / "c" / "d"
        template_dir.mkdir(parents=True)
        resolver = PathResolver(lambda: (root,))
        benchmark(resolver.logical_name_for_template, template_dir, root)

    @pytest.mark.benchmark(group="static.discovery")
    def test_logical_name_for_layout_deep(self, tmp_path: Path, benchmark) -> None:
        root = tmp_path / "pages"
        root.mkdir()
        layout_dir = root / "a" / "b" / "c" / "d"
        layout_dir.mkdir(parents=True)
        resolver = PathResolver(lambda: (root,))
        benchmark(resolver.logical_name_for_layout, layout_dir, root)
