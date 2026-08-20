from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.pages import Page
from next.pages.registry import PageContextRegistry


if TYPE_CHECKING:
    from pathlib import Path


def _context_func() -> dict[str, str]:
    return {"title": "hello", "count": "42"}  # type: ignore[dict-item]


class TestBenchPageContextRegistry:
    @pytest.mark.benchmark(group="pages.registry")
    def test_register_context(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"

        def run() -> None:
            registry = PageContextRegistry(None)
            for i in range(20):
                registry.register_context(page_path, f"k_{i}", _context_func)

        benchmark(run)

    @pytest.mark.benchmark(group="pages.registry")
    def test_context_decorator(self, benchmark) -> None:
        """Import-time cost of `@context`, which attributes each callable to a file."""

        def run() -> None:
            page = Page()
            for i in range(20):
                page.context(f"k_{i}")(_context_func)

        benchmark(run)

    @pytest.mark.benchmark(group="pages.registry")
    def test_collect_context_single(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.touch()
        registry = PageContextRegistry(None)
        registry.register_context(page_path, None, _context_func)
        benchmark(registry.collect_context, page_path)

    @pytest.mark.benchmark(group="pages.registry")
    def test_collect_context_keyed_many(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.touch()
        registry = PageContextRegistry(None)
        for i in range(20):
            registry.register_context(page_path, f"k_{i}", _context_func)
        benchmark(registry.collect_context, page_path)

    @pytest.mark.benchmark(group="pages.registry")
    def test_collect_context_zone_tagged_full_render(
        self, tmp_path: Path, benchmark
    ) -> None:
        """Full render of zone-bound callables, where the filter runs but skips none."""
        page_path = tmp_path / "page.py"
        page_path.touch()
        registry = PageContextRegistry(None)
        for i in range(20):
            registry.register_context(page_path, f"k_{i}", _context_func, zone=f"z_{i}")
        benchmark(registry.collect_context, page_path)
