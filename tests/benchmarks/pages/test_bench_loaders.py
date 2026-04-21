"""Benchmarks for ``next.pages.loaders``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.pages.loaders import (
    DjxTemplateLoader,
    LayoutTemplateLoader,
    PythonTemplateLoader,
    _load_python_module_memo,
)


if TYPE_CHECKING:
    from pathlib import Path


_PY_SRC = "template = 'hello {{ name }}'\n"


class TestBenchPythonModuleLoader:
    @pytest.mark.benchmark(group="pages.loaders")
    def test_python_load_cold(self, tmp_path: Path, benchmark) -> None:
        def run() -> None:
            i = getattr(run, "i", 0)
            run.i = i + 1
            page_path = tmp_path / f"page_{i}.py"
            page_path.write_text(_PY_SRC)
            _load_python_module_memo(page_path)

        benchmark(run)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_python_load_warm_mtime_hit(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text(_PY_SRC)
        _load_python_module_memo(page_path)
        benchmark(_load_python_module_memo, page_path)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_python_template_loader_can_load(
        self,
        tmp_path: Path,
        python_template_loader: PythonTemplateLoader,
        benchmark,
    ) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text(_PY_SRC)
        python_template_loader.can_load(page_path)  # warm memo
        benchmark(python_template_loader.can_load, page_path)


class TestBenchDjxLoader:
    @pytest.mark.benchmark(group="pages.loaders")
    def test_djx_can_load_hit(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text(_PY_SRC)
        (tmp_path / "template.djx").write_text("<h1>{{ name }}</h1>")
        loader = DjxTemplateLoader()
        benchmark(loader.can_load, page_path)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_djx_can_load_miss(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text(_PY_SRC)
        loader = DjxTemplateLoader()
        benchmark(loader.can_load, page_path)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_djx_load_template(self, tmp_path: Path, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text(_PY_SRC)
        (tmp_path / "template.djx").write_text("<h1>{{ name }}</h1>" * 50)
        loader = DjxTemplateLoader()
        benchmark(loader.load_template, page_path)


class TestBenchLayoutLoader:
    @pytest.mark.benchmark(group="pages.loaders")
    def test_ancestor_walk_no_layouts(self, tmp_path: Path, benchmark) -> None:
        leaf = tmp_path
        for i in range(10):
            leaf = leaf / f"d_{i}"
            leaf.mkdir()
        page_path = leaf / "page.py"
        page_path.write_text(_PY_SRC)
        loader = LayoutTemplateLoader()
        benchmark(loader._find_layout_files, page_path)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_ancestor_walk_with_layouts(self, tmp_path: Path, benchmark) -> None:
        leaf = tmp_path
        for i in range(10):
            leaf = leaf / f"d_{i}"
            leaf.mkdir()
            (leaf / "layout.djx").write_text(
                "{% block template %}{% endblock template %}"
            )
        page_path = leaf / "page.py"
        page_path.write_text(_PY_SRC)
        loader = LayoutTemplateLoader()
        benchmark(loader._find_layout_files, page_path)
