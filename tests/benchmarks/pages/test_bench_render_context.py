"""Benchmarks for ``Page.build_render_context`` and the ``page_rendered`` signal.

``build_render_context`` runs on every HTTP page render and collects every
``@context`` value the page (and its layout ancestors) declare. The heavy
cases here show how cost scales with context-function count and how much
``page_rendered.send`` adds when listeners are attached.
"""

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


class TestBenchBuildRenderContext:
    @pytest.mark.parametrize("count", [5, 20], ids=["small", "large"])
    @pytest.mark.benchmark(group="pages.render_context")
    def test_build_context(self, tmp_path: Path, count: int, benchmark) -> None:
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        _register_context_functions(page, page_path, count)
        benchmark(page.build_render_context, page_path)


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
