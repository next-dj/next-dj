from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components import ComponentInfo
from next.static import (
    AssetDiscovery,
    StaticCollector,
    StaticFilesBackend,
)
from next.static.discovery import (
    BackendProvider,
    PathResolver,
    StemRegistry,
    default_stems,
)


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.static import StaticBackend


class _Provider:
    """Concrete BackendProvider for tests."""

    def __init__(self, backend: StaticBackend, roots: tuple[Path, ...]) -> None:
        self._backend = backend
        self._roots = roots

    @property
    def default_backend(self) -> StaticBackend:
        return self._backend

    def page_roots(self) -> tuple[Path, ...]:
        return self._roots


class TestBackendProviderProtocol:
    """Any object with default_backend + page_roots satisfies the protocol."""

    def test_runtime_checkable(self, file_backend: StaticBackend) -> None:
        provider = _Provider(file_backend, ())
        assert isinstance(provider, BackendProvider)

    def test_non_conforming_object_fails(self) -> None:
        assert not isinstance(object(), BackendProvider)


class TestStemRegistryDefaults:
    def test_default_roles(self) -> None:
        reg = StemRegistry()
        assert reg.stems("template") == ("template",)
        assert reg.stems("layout") == ("layout",)
        assert reg.stems("component") == ("component",)

    def test_unknown_role_returns_empty(self) -> None:
        reg = StemRegistry()
        assert reg.stems("ghost") == ()

    def test_roles_returns_all(self) -> None:
        reg = StemRegistry()
        assert set(reg.roles()) == {"template", "layout", "component"}


class TestStemRegistryRegister:
    def test_add_stem_to_existing_role(self) -> None:
        reg = StemRegistry()
        reg.register("template", "page")
        assert reg.stems("template") == ("template", "page")

    def test_add_stem_creates_role(self) -> None:
        reg = StemRegistry()
        reg.register("meta", "head")
        assert "meta" in reg.roles()
        assert reg.stems("meta") == ("head",)

    def test_register_is_idempotent(self) -> None:
        reg = StemRegistry()
        reg.register("template", "page")
        reg.register("template", "page")
        assert reg.stems("template") == ("template", "page")


class TestDefaultStems:
    def test_is_stem_registry(self) -> None:
        assert isinstance(default_stems, StemRegistry)

    def test_preserves_core_roles(self) -> None:
        assert "template" in default_stems.roles()


class TestPathResolverFindPageRoot:
    def test_returns_matching_root(self, tmp_path: Path) -> None:
        (tmp_path / "page.djx").write_text("<div/>")
        resolver = PathResolver(lambda: (tmp_path.resolve(),))
        assert resolver.find_page_root(tmp_path / "page.djx") == tmp_path.resolve()

    def test_returns_none_when_outside(self, tmp_path: Path) -> None:
        elsewhere = tmp_path.parent
        resolver = PathResolver(lambda: (tmp_path.resolve() / "other",))
        assert resolver.find_page_root(elsewhere / "x.djx") is None

    def test_picks_first_matching_root(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.mkdir()
        b = tmp_path / "b"
        b.mkdir()
        resolver = PathResolver(lambda: (a.resolve(), b.resolve()))
        (a / "p.djx").write_text("")
        assert resolver.find_page_root(a / "p.djx") == a.resolve()


class TestPathResolverLogicalNameForTemplate:
    def test_root_level_template(self, tmp_path: Path) -> None:
        resolver = PathResolver(lambda: (tmp_path.resolve(),))
        assert resolver.logical_name_for_template(tmp_path, tmp_path) == "index"

    def test_nested_template(self, tmp_path: Path) -> None:
        nested = tmp_path / "blog" / "post"
        nested.mkdir(parents=True)
        resolver = PathResolver(lambda: (tmp_path.resolve(),))
        assert resolver.logical_name_for_template(nested, tmp_path) == "blog/post"

    def test_no_root_fallbacks_to_directory_name(self, tmp_path: Path) -> None:
        nested = tmp_path / "about"
        nested.mkdir()
        resolver = PathResolver(lambda: ())
        assert resolver.logical_name_for_template(nested, None) == "about"


class TestPathResolverLogicalNameForLayout:
    def test_root_level_layout(self, tmp_path: Path) -> None:
        resolver = PathResolver(lambda: (tmp_path.resolve(),))
        assert resolver.logical_name_for_layout(tmp_path, tmp_path) == "layout"

    def test_nested_layout(self, tmp_path: Path) -> None:
        nested = tmp_path / "docs"
        nested.mkdir()
        resolver = PathResolver(lambda: (tmp_path.resolve(),))
        assert resolver.logical_name_for_layout(nested, tmp_path) == "docs/layout"

    def test_no_root_uses_fallback(self, tmp_path: Path) -> None:
        nested = tmp_path / "section"
        nested.mkdir()
        resolver = PathResolver(lambda: ())
        assert resolver.logical_name_for_layout(nested, None) == "section/layout"


class TestAssetDiscoveryPageTemplate:
    """template.css/js are collected from the page directory."""

    def test_collects_template_css_and_js(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
    ) -> None:
        (tmp_path / "template.css").write_text("body{}")
        (tmp_path / "template.js").write_text("/* js */")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)

        style_urls = [a.url for a in collector.styles()]
        script_urls = [a.url for a in collector.scripts()]
        assert style_urls == ["/static/next/index.css"]
        assert script_urls == ["/static/next/index.js"]

    def test_missing_files_are_skipped(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
    ) -> None:
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert collector.styles() == []
        assert collector.scripts() == []


class TestAssetDiscoveryLayoutChain:
    """Outer-most layout is collected before inner layouts and template."""

    def test_layouts_come_before_template(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
    ) -> None:
        (tmp_path / "layout.djx").write_text("")
        (tmp_path / "layout.css").write_text("")
        nested = tmp_path / "section"
        nested.mkdir()
        (nested / "layout.djx").write_text("")
        (nested / "layout.css").write_text("")
        page_dir = nested / "post"
        page_dir.mkdir()
        (page_dir / "template.css").write_text("")
        page_path = page_dir / "page.djx"
        page_path.write_text("")

        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        urls = [a.url for a in collector.styles()]
        assert urls == [
            "/static/next/layout.css",
            "/static/next/section/layout.css",
            "/static/next/section/post.css",
        ]


class TestAssetDiscoveryModuleLists:
    """styles/scripts list vars in page.py are appended to the collector."""

    def test_reads_styles_and_scripts(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
    ) -> None:
        page_dir = tmp_path / "about"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text(
            'styles = ["https://cdn.example.com/x.css"]\n'
            'scripts = ["https://cdn.example.com/x.js"]\n'
        )

        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.styles()] == ["https://cdn.example.com/x.css"]
        assert [a.url for a in collector.scripts()] == ["https://cdn.example.com/x.js"]

    def test_module_list_cache_skips_reparse(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
    ) -> None:
        page_dir = tmp_path / "cached"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('styles = ["https://c.example/a.css"]\n')

        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)

        page_path.write_text('styles = ["https://c.example/changed.css"]\n')

        collector2 = StaticCollector()
        discovery.discover_page_assets(page_path, collector2)
        assert [a.url for a in collector2.styles()] == ["https://c.example/a.css"]

    def test_module_list_and_layout_caches_evict_oldest(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
        monkeypatch,
    ) -> None:
        """Both module-list and layout-dir caches drop the oldest key past the limit."""
        from next.static import discovery as discovery_mod

        monkeypatch.setattr(discovery_mod, "_MODULE_LIST_CACHE_MAX_SIZE", 1)
        monkeypatch.setattr(discovery_mod, "_LAYOUT_DIR_CACHE_MAX_SIZE", 1)

        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        collector = StaticCollector()

        # Two parseable pages exercise the happy-path eviction (line after the
        # successful cache write). A third, broken page exercises the "module is
        # None" eviction branch.
        for i in range(2):
            ok_dir = tmp_path / f"ok_{i}"
            ok_dir.mkdir()
            ok_page = ok_dir / "page.py"
            ok_page.write_text(f'styles = ["https://c.example/a{i}.css"]\n')
            discovery.discover_page_assets(ok_page, collector)

        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        broken_page = broken_dir / "page.py"
        broken_page.write_text("this is not valid python =====\n")
        discovery.discover_page_assets(broken_page, collector)

        # Oldest module-list key evicted after exceeding max size.
        assert len(discovery._module_list_cache) <= 1

        # Layout-dir cache eviction runs once per page with layouts above it.
        for i in range(3):
            sub = tmp_path / f"layout_case_{i}"
            sub.mkdir()
            (sub / "layout.djx").write_text("<x/>")
            page = sub / "page.py"
            page.write_text("")
            discovery._find_layout_directories(page.resolve(), tmp_path.resolve())
        assert len(discovery._layout_dir_cache) <= 1


class TestAssetDiscoveryComponents:
    def test_simple_component_yields_nothing(
        self,
        file_backend: StaticBackend,
        simple_component: ComponentInfo,
    ) -> None:
        provider = _Provider(file_backend, ())
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_component_assets(simple_component, collector)
        assert collector.styles() == []
        assert collector.scripts() == []

    def test_composite_component_picks_up_css_js_and_module_lists(
        self,
        file_backend: StaticBackend,
        composite_component: ComponentInfo,
    ) -> None:
        provider = _Provider(file_backend, ())
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_component_assets(composite_component, collector)

        style_urls = [a.url for a in collector.styles()]
        script_urls = [a.url for a in collector.scripts()]
        assert "/static/next/components/widget.css" in style_urls
        assert "/static/next/components/widget.js" in script_urls
        assert "https://cdn.example.com/extra.css" in style_urls
        assert "https://cdn.example.com/extra.js" in script_urls


class _FailingBackend(StaticFilesBackend):
    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        msg = "cannot resolve"
        raise ValueError(msg)


class TestAssetDiscoveryErrorHandling:
    def test_warning_logged_on_value_error(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")

        provider = _Provider(_FailingBackend(), (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        with caplog.at_level("WARNING", logger="next.static.discovery"):
            discovery.discover_page_assets(page_path, collector)

        assert collector.styles() == []
        assert any(
            "Failed to register static asset" in r.getMessage() for r in caplog.records
        )


class TestAssetDiscoveryCustomStems:
    def test_custom_template_stem_is_picked_up(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
    ) -> None:
        stems = StemRegistry()
        stems.register("template", "page")
        (tmp_path / "page.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")

        provider = _Provider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider, stems=stems)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.styles()] == ["/static/next/index.css"]


class TestMakeDiscoveryFixture:
    """Ensure the conftest helper builds a wired-up AssetDiscovery."""

    def test_factory_produces_usable_pair(
        self,
        file_backend: StaticBackend,
        make_discovery: Callable[..., object],
    ) -> None:
        discovery, manager = make_discovery(file_backend)  # type: ignore[misc]
        assert isinstance(discovery, AssetDiscovery)
        assert manager.default_backend is file_backend
