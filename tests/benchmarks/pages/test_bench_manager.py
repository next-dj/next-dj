"""Benchmarks for ``next.pages.manager.Page``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.pages import Page


if TYPE_CHECKING:
    from pathlib import Path


_SIMPLE_TEMPLATE = "<h1>{{ title }}</h1>"
_HEAVY_TEMPLATE = "".join(f"<p>{{{{ v_{i} }}}}</p>" for i in range(50))


class TestBenchPageRender:
    @pytest.mark.benchmark(group="pages.render")
    def test_render_simple(
        self, tmp_path: Path, page_instance: Page, benchmark
    ) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        page_instance.register_template(page_path, _SIMPLE_TEMPLATE)
        benchmark(page_instance.render, page_path, title="Bench")

    @pytest.mark.benchmark(group="pages.render")
    def test_render_heavy_context(
        self, tmp_path: Path, page_instance: Page, benchmark
    ) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        page_instance.register_template(page_path, _HEAVY_TEMPLATE)
        kwargs = {f"v_{i}": f"val_{i}" for i in range(50)}
        benchmark(page_instance.render, page_path, **kwargs)

    @pytest.mark.benchmark(group="pages.render")
    def test_build_render_context(
        self, tmp_path: Path, page_instance: Page, benchmark
    ) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        benchmark(page_instance.build_render_context, page_path, title="Bench")
