from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.pages import Page
from next.pages.signals import page_rendered
from tests.benchmarks.factories import noop_signal_receiver


if TYPE_CHECKING:
    from pathlib import Path


_SIMPLE_TEMPLATE = "<h1>{{ title }}</h1>"


def _register_context_functions(page: Page, page_path, count: int) -> None:
    """Attach ``count`` keyed context callables to ``page_path``."""
    for i in range(count):
        key = f"k_{i}"

        def _ctx(i=i) -> dict[str, int]:
            return {"value": i}

        page._context_manager.register_context(page_path, key, _ctx)


def _inherited_value() -> str:
    """Inheritable context callable priced near zero, so the walk stays visible."""
    return "v"


def _build_ancestor_chain(page: Page, root: Path, depth: int, inherited: int) -> Path:
    """Write ``depth`` ancestor ``page.py`` files above a leaf page and return the leaf.

    Every ancestor registers ``inherited`` inheritable callables, so a run at
    zero separates the walk itself from the callables it finds.
    """
    directory = root
    for i in range(depth):
        ancestor = directory / "page.py"
        ancestor.write_text("x = 1\n")
        for j in range(inherited):
            page._context_manager.register_context(
                ancestor, f"a_{i}_{j}", _inherited_value, inherit_context=True
            )
        directory = directory / f"seg_{i}"
        directory.mkdir()
    leaf = directory / "page.py"
    leaf.write_text("x = 1\n")
    return leaf


class TestBenchBuildRenderContext:
    @pytest.mark.parametrize("count", [5, 20], ids=["small", "large"])
    @pytest.mark.benchmark(group="pages.render_context")
    def test_build_context(self, tmp_path: Path, count: int, benchmark) -> None:
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        _register_context_functions(page, page_path, count)
        benchmark(page.build_render_context, page_path)


class TestBenchInheritedWalk:
    """Ancestor walk of `build_render_context` against the depth of the page tree."""

    @pytest.mark.parametrize("depth", [1, 8, 32], ids=["d1", "d8", "d32"])
    @pytest.mark.parametrize("inherited", [0, 1], ids=["plain", "inherit"])
    @pytest.mark.benchmark(group="pages.render_context")
    def test_inherited_walk_depth(
        self, tmp_path: Path, depth: int, inherited: int, benchmark
    ) -> None:
        page = Page()
        leaf = _build_ancestor_chain(page, tmp_path, depth, inherited)
        benchmark(page.build_render_context, leaf)


class TestBenchPageRenderedSignal:
    @pytest.mark.benchmark(group="pages.signals")
    def test_render_no_receiver(self, tmp_path: Path, benchmark) -> None:
        """Full ``page.render`` cost with no ``page_rendered`` listener."""
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        page.register_template(page_path, _SIMPLE_TEMPLATE)
        benchmark(page.render, page_path, title="bench")

    @pytest.mark.benchmark(group="pages.signals")
    def test_render_with_receiver(self, tmp_path: Path, benchmark) -> None:
        """Full ``page.render`` cost with one ``page_rendered`` listener."""
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        page.register_template(page_path, _SIMPLE_TEMPLATE)
        page_rendered.connect(noop_signal_receiver)
        try:
            benchmark(page.render, page_path, title="bench")
        finally:
            page_rendered.disconnect(noop_signal_receiver)

    @pytest.mark.benchmark(group="pages.signals")
    def test_render_with_receiver_large_context(
        self, tmp_path: Path, benchmark
    ) -> None:
        """``page_rendered`` kwarg overhead with a 20-key context.

        Shows how ``tuple(context_data.keys())`` in the send kwargs scales
        with context size. Difference against ``test_render_no_receiver``
        (or the small-context variant above) isolates the receiver cost.
        """
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        page.register_template(page_path, _SIMPLE_TEMPLATE)
        _register_context_functions(page, page_path, 20)
        page_rendered.connect(noop_signal_receiver)
        try:
            benchmark(page.render, page_path, title="bench")
        finally:
            page_rendered.disconnect(noop_signal_receiver)
