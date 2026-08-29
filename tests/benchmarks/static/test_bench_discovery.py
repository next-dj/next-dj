from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.static import AssetDiscovery, StaticCollector, StaticFilesBackend
from next.static.assets import default_kinds
from next.static.discovery import PathResolver
from tests.benchmarks.factories import build_layout_page


if TYPE_CHECKING:
    from pathlib import Path

    from next.static import StaticBackend


class _StubBackend(StaticFilesBackend):
    """Backend with deterministic URLs, so a run times discovery and nothing else."""

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the URL the logical name and kind spell out."""
        del source_path
        return f"/static/next/{logical_name}{default_kinds.extension(kind)}"


class _Provider:
    """The narrow provider `AssetDiscovery` reads, holding resolved roots."""

    def __init__(self, backend: StaticBackend, roots: tuple[Path, ...]) -> None:
        self._backend = backend
        self._roots = roots

    @property
    def default_backend(self) -> StaticBackend:
        """Return the backend every registration goes through."""
        return self._backend

    def page_roots(self) -> tuple[Path, ...]:
        """Return the resolved page trees the discovery walks within."""
        return self._roots


def _discovery_for(tmp_path: Path, *, assets: bool) -> tuple[AssetDiscovery, Path]:
    """Build a warm discovery over a page nested under two layouts."""
    page_file = build_layout_page(tmp_path, layouts=2, template="<h1>x</h1>")
    if assets:
        (page_file.parent / "template.css").write_text("body{}")
        (page_file.parent / "template.js").write_text("/* js */")
        (tmp_path / "layout.css").write_text("body{}")
    discovery = AssetDiscovery(_Provider(_StubBackend(), (tmp_path.resolve(),)))
    discovery.discover_page_assets(page_file, StaticCollector())
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
