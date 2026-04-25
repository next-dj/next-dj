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


if TYPE_CHECKING:
    from pathlib import Path


_SIMPLE_TEMPLATE = "<h1>{{ title }}</h1>"


def _noop_receiver(sender: object, **_: object) -> None:  # pragma: no cover
    del sender


def _register_context_functions(page: Page, page_path, count: int) -> None:
    """Attach ``count`` keyed context callables to ``page_path``."""
    for i in range(count):
        key = f"k_{i}"

        def _ctx(i=i) -> dict[str, int]:
            return {"value": i}

        page._context_manager.register_context(page_path, key, _ctx)


class TestBenchBuildRenderContext:
    @pytest.mark.benchmark(group="pages.render_context")
    def test_build_small_context(self, tmp_path: Path, benchmark) -> None:
        """Cold build with 5 ``@context`` callables."""
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        _register_context_functions(page, page_path, 5)
        benchmark(page.build_render_context, page_path)

    @pytest.mark.benchmark(group="pages.render_context")
    def test_build_large_context(self, tmp_path: Path, benchmark) -> None:
        """Heavy case with 20 ``@context`` callables."""
        page = Page()
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        _register_context_functions(page, page_path, 20)
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
        page_rendered.connect(_noop_receiver)
        try:
            benchmark(page.render, page_path, title="bench")
        finally:
            page_rendered.disconnect(_noop_receiver)

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
        page_rendered.connect(_noop_receiver)
        try:
            benchmark(page.render, page_path, title="bench")
        finally:
            page_rendered.disconnect(_noop_receiver)
