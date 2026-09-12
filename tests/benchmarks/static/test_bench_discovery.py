from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.static import AssetDiscovery, StaticCollector
from next.static.discovery import PathResolver
from tests.benchmarks.factories import build_layout_page
from tests.support import PlainStaticBackend, StaticAssetProvider, component_info


if TYPE_CHECKING:
    from pathlib import Path

    from next.components import ComponentInfo


# A cold round builds a plan from scratch, so the count stays modest and the
# warmup rounds keep the page-module import out of the reported median.
_COLD_ROUNDS = 50
_COLD_WARMUP_ROUNDS = 5


def _new_discovery(root: Path) -> AssetDiscovery:
    """Return a discovery holding no plan, rooted at `root`."""
    return AssetDiscovery(StaticAssetProvider(PlainStaticBackend(), (root.resolve(),)))


def _build_page_tree(tmp_path: Path, *, assets: bool) -> Path:
    """Write a page nested under two layouts and return its `page.py`."""
    page_file = build_layout_page(tmp_path, layouts=2, template="<h1>x</h1>")
    if assets:
        (page_file.parent / "template.css").write_text("body{}")
        (page_file.parent / "template.js").write_text("/* js */")
        (tmp_path / "layout.css").write_text("body{}")
    return page_file


def _discover_page(discovery: AssetDiscovery, page_file: Path) -> None:
    """Run one page discovery into a request-sized collector."""
    discovery.discover_page_assets(page_file, StaticCollector())


def _discover_component(discovery: AssetDiscovery, info: ComponentInfo) -> None:
    """Run one component discovery into a request-sized collector."""
    discovery.discover_component_assets(info, StaticCollector())


def _discovery_for(tmp_path: Path, *, assets: bool) -> tuple[AssetDiscovery, Path]:
    """Build a warm discovery over a page nested under two layouts."""
    page_file = _build_page_tree(tmp_path, assets=assets)
    discovery = _new_discovery(tmp_path)
    _discover_page(discovery, page_file)
    return discovery, page_file


class TestBenchPageAssetDiscovery:
    """Per-render cost of `discover_page_assets`, the path every page GET walks."""

    @pytest.mark.benchmark(group="static.discovery")
    def test_page_assets_warm_bare(self, tmp_path: Path, benchmark) -> None:
        """A page carrying no co-located asset, the shape probing dominates."""
        discovery, page_file = _discovery_for(tmp_path, assets=False)
        benchmark(discovery.discover_page_assets, page_file, StaticCollector())

    @pytest.mark.benchmark(group="static.discovery")
    def test_page_assets_warm_with_assets(self, tmp_path: Path, benchmark) -> None:
        discovery, page_file = _discovery_for(tmp_path, assets=True)
        benchmark(discovery.discover_page_assets, page_file, StaticCollector())

    @pytest.mark.benchmark(group="static.discovery")
    def test_page_assets_warm_debug(self, tmp_path: Path, benchmark) -> None:
        """The same page under `DEBUG`, where the plan stats its directories."""
        discovery, page_file = _discovery_for(tmp_path, assets=True)
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_file, StaticCollector())
            benchmark(discovery.discover_page_assets, page_file, StaticCollector())


def _component_for(tmp_path: Path) -> tuple[AssetDiscovery, ComponentInfo]:
    """Build a warm discovery over a composite component with both asset kinds."""
    comp_dir = tmp_path / "_components" / "widget"
    comp_dir.mkdir(parents=True)
    (comp_dir / "component.css").write_text(".widget{}")
    (comp_dir / "component.js").write_text("/* widget */")
    info = component_info(comp_dir, template="<div>widget</div>")
    discovery = _new_discovery(tmp_path)
    _discover_component(discovery, info)
    return discovery, info


class TestBenchComponentAssetDiscovery:
    """Per-instance cost of `discover_component_assets`, paid by every tag render."""

    @pytest.mark.benchmark(group="static.discovery")
    def test_component_assets_warm(self, tmp_path: Path, benchmark) -> None:
        discovery, info = _component_for(tmp_path)
        benchmark(discovery.discover_component_assets, info, StaticCollector())

    @pytest.mark.benchmark(group="static.discovery")
    def test_component_assets_repeated_instance(
        self, tmp_path: Path, benchmark
    ) -> None:
        """A second instance on the same page, sharing the request collector."""
        discovery, info = _component_for(tmp_path)
        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        benchmark(discovery.discover_component_assets, info, collector)

    @pytest.mark.benchmark(group="static.discovery")
    def test_component_assets_warm_debug(self, tmp_path: Path, benchmark) -> None:
        """The same component under `DEBUG`, where the plan stats its folder."""
        discovery, info = _component_for(tmp_path)
        with override_settings(DEBUG=True):
            discovery.discover_component_assets(info, StaticCollector())
            benchmark(discovery.discover_component_assets, info, StaticCollector())


class TestBenchColdAssetPlan:
    """What building an `_AssetPlan` costs, against the replay of a built one.

    A cold round hands the target a discovery that holds no plan and no page
    root, the shape the first render after a reload meets. The rebuild round
    keeps the resolver warm and drops only the plan, which is what a touched
    asset directory leaves behind.
    """

    @pytest.mark.benchmark(group="static.discovery")
    def test_page_plan_cold_build(self, tmp_path: Path, benchmark) -> None:
        page_file = _build_page_tree(tmp_path, assets=True)

        def setup() -> tuple[tuple[AssetDiscovery, Path], dict[str, object]]:
            return (_new_discovery(tmp_path), page_file), {}

        benchmark.pedantic(
            _discover_page,
            setup=setup,
            rounds=_COLD_ROUNDS,
            warmup_rounds=_COLD_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="static.discovery")
    def test_page_plan_rebuild_after_invalidation(
        self, tmp_path: Path, benchmark
    ) -> None:
        discovery, page_file = _discovery_for(tmp_path, assets=True)

        def setup() -> tuple[tuple[AssetDiscovery, Path], dict[str, object]]:
            discovery._page_plan_cache.clear()
            return (discovery, page_file), {}

        benchmark.pedantic(
            _discover_page,
            setup=setup,
            rounds=_COLD_ROUNDS,
            warmup_rounds=_COLD_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="static.discovery")
    def test_component_plan_cold_build(self, tmp_path: Path, benchmark) -> None:
        _discovery, info = _component_for(tmp_path)

        def setup() -> tuple[tuple[AssetDiscovery, ComponentInfo], dict[str, object]]:
            return (_new_discovery(tmp_path), info), {}

        benchmark.pedantic(
            _discover_component,
            setup=setup,
            rounds=_COLD_ROUNDS,
            warmup_rounds=_COLD_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="static.discovery")
    def test_component_plan_rebuild_after_invalidation(
        self, tmp_path: Path, benchmark
    ) -> None:
        discovery, info = _component_for(tmp_path)

        def setup() -> tuple[tuple[AssetDiscovery, ComponentInfo], dict[str, object]]:
            discovery._component_plan_cache.clear()
            return (discovery, info), {}

        benchmark.pedantic(
            _discover_component,
            setup=setup,
            rounds=_COLD_ROUNDS,
            warmup_rounds=_COLD_WARMUP_ROUNDS,
        )


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
