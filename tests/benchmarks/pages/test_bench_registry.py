from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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
