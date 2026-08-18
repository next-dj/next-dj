from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.template import Context
from django.template.engine import Engine


if TYPE_CHECKING:
    from pathlib import Path

    from next.pages import Page


_SOURCE = "<h1>{{ title }}</h1>"
_CONTEXT = {"title": "Bench"}


class TestBenchAgainstDjango:
    """One template rendered by plain Django and by the page pipeline.

    The rows price what the framework adds over a bare Django render, which
    is layout composition, the context registry, and asset discovery.
    """

    @pytest.mark.benchmark(group="pages.baseline")
    def test_django_compiled_template(self, benchmark) -> None:
        template = Engine.get_default().from_string(_SOURCE)
        benchmark(template.render, Context(_CONTEXT))

    @pytest.mark.benchmark(group="pages.baseline")
    def test_django_file_template(self, tmp_path: Path, benchmark) -> None:
        (tmp_path / "bench.html").write_text(_SOURCE)
        engine = Engine(dirs=[str(tmp_path)])
        template = engine.get_template("bench.html")
        benchmark(template.render, Context(_CONTEXT))

    @pytest.mark.benchmark(group="pages.baseline")
    def test_next_page_render(
        self, tmp_path: Path, page_instance: Page, benchmark
    ) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text("def render(r): return 'x'\n")
        page_instance.register_template(page_path, _SOURCE)
        benchmark(page_instance.render, page_path, **_CONTEXT)
