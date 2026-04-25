"""Benchmarks for ``next.pages.loaders``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.pages.loaders import (
    DjxTemplateLoader,
    LayoutTemplateLoader,
    PythonTemplateLoader,
    _load_python_module_memo,
    build_registered_loaders,
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


class TestBenchLoaderChain:
    """``TEMPLATE_LOADERS`` resolution and iteration through `can_load`."""

    @pytest.mark.benchmark(group="pages.loaders")
    def test_build_registered_loaders_warm(self, benchmark) -> None:
        """Memoised lookup — should be O(1) after the first call."""
        build_registered_loaders()
        benchmark(build_registered_loaders)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_chain_first_hit_wins(self, tmp_path: Path, benchmark) -> None:
        """``.djx`` loader responds on the first try (common happy-path)."""
        page_path = tmp_path / "page.py"
        page_path.write_text(_PY_SRC)
        (tmp_path / "template.djx").write_text("<h1>{{ name }}</h1>")
        loaders = build_registered_loaders()

        def run() -> None:
            for loader in loaders:
                if loader.can_load(page_path):
                    loader.load_template(page_path)
                    return

        benchmark(run)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_chain_miss_then_hit(self, tmp_path: Path, benchmark) -> None:
        """No ``.djx`` sibling — chain must fall through to a layout."""
        leaf = tmp_path / "d_0"
        leaf.mkdir()
        (leaf / "layout.djx").write_text("{% block template %}{% endblock template %}")
        page_path = leaf / "page.py"
        page_path.write_text(_PY_SRC)
        loaders = build_registered_loaders()

        def run() -> None:
            for loader in loaders:
                if loader.can_load(page_path):
                    loader.load_template(page_path)
                    return

        benchmark(run)


class TestBenchComposeLayoutHierarchy:
    """``_compose_layout_hierarchy`` cost grows linearly with depth."""

    @staticmethod
    def _build_layouts(tmp_path: Path, depth: int) -> tuple[Path, list[Path]]:
        leaf = tmp_path
        layouts: list[Path] = []
        for i in range(depth):
            leaf = leaf / f"d_{i}"
            leaf.mkdir()
            layout = leaf / "layout.djx"
            layout.write_text("{% block template %}{% endblock template %}")
            layouts.append(layout)
        page_path = leaf / "page.py"
        page_path.write_text(_PY_SRC)
        return page_path, layouts

    @pytest.mark.benchmark(group="pages.loaders")
    def test_compose_depth_3(self, tmp_path: Path, benchmark) -> None:
        _page, layouts = self._build_layouts(tmp_path, 3)
        loader = LayoutTemplateLoader()
        benchmark(loader._compose_layout_hierarchy, "<body/>", layouts)

    @pytest.mark.benchmark(group="pages.loaders")
    def test_compose_depth_10(self, tmp_path: Path, benchmark) -> None:
        _page, layouts = self._build_layouts(tmp_path, 10)
        loader = LayoutTemplateLoader()
        benchmark(loader._compose_layout_hierarchy, "<body/>", layouts)
