from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from django.test import override_settings

from next.components import ComponentInfo
from next.static import (
    AssetDiscovery,
    StaticCollector,
    StaticFilesBackend,
    default_kinds,
    discovery as discovery_mod,
)
from next.static.collector import HashContentDedup, default_placeholders
from next.static.discovery import (
    BackendProvider,
    PathResolver,
    StemRegistry,
    default_stems,
)
from next.static.signals import asset_registered
from next.urls import FileRouterBackend
from tests.support import (
    RecordingStaticBackend,
    StaticAssetProvider,
    component_info,
    record_path_calls,
    restored_static_registries,
)


if TYPE_CHECKING:
    import pytest

    from next.static import StaticBackend
    from next.static.discovery import _AssetPlan


class TestBackendProviderProtocol:
    """Any object with default_backend + page_roots satisfies the protocol."""

    def test_runtime_checkable(self, file_backend: StaticBackend) -> None:
        provider = StaticAssetProvider(file_backend, ())
        assert isinstance(provider, BackendProvider)

    def test_non_conforming_object_fails(self) -> None:
        assert not isinstance(object(), BackendProvider)

    def test_a_router_backend_is_not_a_provider(self) -> None:
        # Both carry `page_roots`, but a match also needs `default_backend`.
        assert not isinstance(FileRouterBackend(app_dirs=False), BackendProvider)


class TestStemRegistryDefaults:
    def test_default_roles(self) -> None:
        reg = StemRegistry()
        assert reg.stems("template") == ("template",)
        assert reg.stems("layout") == ("layout",)
        assert reg.stems("component") == ("component",)

    def test_unknown_role_returns_empty(self) -> None:
        reg = StemRegistry()
        assert reg.stems("ghost") == ()


class TestStemRegistryRegister:
    def test_add_stem_to_existing_role(self) -> None:
        reg = StemRegistry()
        reg.register("template", "page")
        assert reg.stems("template") == ("template", "page")

    def test_add_stem_creates_role(self) -> None:
        reg = StemRegistry()
        reg.register("meta", "head")
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
        assert default_stems.stems("template") == ("template",)
        assert default_stems.stems("layout") == ("layout",)
        assert default_stems.stems("component") == ("component",)


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
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        (tmp_path / "template.css").write_text("body{}")
        (tmp_path / "template.js").write_text("/* js */")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)

        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        script_urls = [a.url for a in collector.assets_in_slot("scripts")]
        assert style_urls == ["/static/next/index.css"]
        assert script_urls == ["/static/next/index.js"]

    def test_missing_files_are_skipped(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("styles") == []
        assert collector.assets_in_slot("scripts") == []


class TestAssetDiscoveryLayoutChain:
    """Outer-most layout is collected before inner layouts and template."""

    def test_layouts_come_before_template(
        self, tmp_path: Path, file_backend: StaticBackend
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

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        urls = [a.url for a in collector.assets_in_slot("styles")]
        assert urls == [
            "/static/next/layout.css",
            "/static/next/section/layout.css",
            "/static/next/section/post.css",
        ]


class TestAssetDiscoveryModuleLists:
    """styles/scripts list vars in page.py are appended to the collector."""

    def test_reads_styles_and_scripts(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_dir = tmp_path / "about"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text(
            'styles = ["https://cdn.example.com/x.css"]\n'
            'scripts = ["https://cdn.example.com/x.js"]\n'
        )

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "https://cdn.example.com/x.css"
        ]
        assert [a.url for a in collector.assets_in_slot("scripts")] == [
            "https://cdn.example.com/x.js"
        ]

    def test_off_debug_an_edited_module_list_stays_invisible(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """The plan holding the module URLs is what freezes them, not a reader."""
        page_dir = tmp_path / "cached"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('styles = ["https://c.example/a.css"]\n')

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)

        page_path.write_text('styles = ["https://c.example/changed.css"]\n')

        collector2 = StaticCollector()
        discovery.discover_page_assets(page_path, collector2)
        assert [a.url for a in collector2.assets_in_slot("styles")] == [
            "https://c.example/a.css"
        ]

    def test_debug_reads_an_edited_module_list_on_the_rebuild(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A `styles` list edited under a live server is read with the new plan."""
        page_dir = tmp_path / "edited"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('styles = ["https://c.example/a.css"]\n')

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())

            page_path.write_text('styles = ["https://c.example/changed.css"]\n')
            # Rewriting a file moves neither its own directory's mtime past the
            # snapshot nor the file past the mtime the module memo keyed on.
            _bump(page_path)
            _bump(page_dir)

            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "https://c.example/changed.css"
        ]

    def test_a_module_that_does_not_execute_contributes_nothing(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A `page.py` that fails to import leaves the page with no module URLs."""
        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        broken_page = broken_dir / "page.py"
        broken_page.write_text("this is not valid python =====\n")

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(broken_page, collector)
        assert collector.assets_in_slot("styles") == []
        assert discovery._page_plan_cache[broken_page].module_assets == ()


class TestAssetDiscoveryPagePlanCache:
    """The per-page plan is walked once and re-probed only under `DEBUG`."""

    @staticmethod
    def _page_with_layout(tmp_path: Path) -> Path:
        (tmp_path / "layout.djx").write_text("")
        page_dir = tmp_path / "section"
        page_dir.mkdir()
        page_path = page_dir / "page.djx"
        page_path.write_text("")
        return page_path

    def test_an_asset_added_later_stays_invisible_off_debug(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_path = self._page_with_layout(tmp_path)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        discovery.discover_page_assets(page_path, StaticCollector())

        (page_path.parent / "template.css").write_text("body{}")

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("styles") == []

    def test_debug_notices_an_asset_that_appeared(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_path = self._page_with_layout(tmp_path)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())

            (page_path.parent / "template.css").write_text("body{}")
            moved = page_path.parent.stat().st_mtime + 10
            os.utime(page_path.parent, (moved, moved))

            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/section.css"
        ]

    def test_debug_keeps_the_plan_while_no_directory_moves(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_path = self._page_with_layout(tmp_path)
        (page_path.parent / "template.css").write_text("body{}")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())
            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/section.css"
        ]

    def test_debug_rebuilds_when_a_directory_mtime_moves_backwards(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A tree restored from a backup carries older mtimes than the plan holds."""
        page_path = self._page_with_layout(tmp_path)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())

            (page_path.parent / "template.css").write_text("body{}")
            _rewind(page_path.parent)

            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/section.css"
        ]

    def test_debug_rebuilds_when_the_page_tree_is_gone(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_path = self._page_with_layout(tmp_path)
        (page_path.parent / "template.css").write_text("body{}")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())
            # The layout directory above keeps its mtime, so the walk reaches
            # the page directory and finds it gone rather than merely moved.
            held = tmp_path.stat().st_mtime
            shutil.rmtree(page_path.parent)
            os.utime(tmp_path, (held, held))

            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("styles") == []

    def test_the_plan_cache_evicts_the_oldest_key(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        monkeypatch.setattr(discovery_mod, "_PAGE_PLAN_CACHE_MAX_SIZE", 1)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        for i in range(2):
            page_dir = tmp_path / f"p_{i}"
            page_dir.mkdir()
            page_path = page_dir / "page.djx"
            page_path.write_text("")
            discovery.discover_page_assets(page_path, StaticCollector())
        assert len(discovery._page_plan_cache) <= 1

    def test_a_warm_render_does_not_walk_the_disk_again(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        page_path = self._page_with_layout(tmp_path)
        (page_path.parent / "template.css").write_text("body{}")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        discovery.discover_page_assets(page_path, StaticCollector())

        builds: list[Path] = []
        walk_disk = discovery._build_page_asset_plan

        def _record(file_path: Path) -> _AssetPlan:
            builds.append(file_path)
            return walk_disk(file_path)

        monkeypatch.setattr(discovery, "_build_page_asset_plan", _record)
        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert builds == []
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/section.css"
        ]

    def test_a_rebuilt_plan_becomes_the_freshest_entry(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        monkeypatch.setattr(discovery_mod, "_PAGE_PLAN_CACHE_MAX_SIZE", 2)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        pages: list[Path] = []
        for name in ("first", "second", "third"):
            page_dir = tmp_path / name
            page_dir.mkdir()
            page_path = page_dir / "page.djx"
            page_path.write_text("")
            pages.append(page_path)

        with override_settings(DEBUG=True):
            discovery.discover_page_assets(pages[0], StaticCollector())
            discovery.discover_page_assets(pages[1], StaticCollector())
            _bump(pages[0].parent)
            discovery.discover_page_assets(pages[0], StaticCollector())
            discovery.discover_page_assets(pages[2], StaticCollector())

        assert list(discovery._page_plan_cache) == [pages[0], pages[2]]

    def test_a_warm_hit_keeps_a_full_cache_in_use_order(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        monkeypatch.setattr(discovery_mod, "_PAGE_PLAN_CACHE_MAX_SIZE", 2)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        pages: list[Path] = []
        for name in ("first", "second", "third"):
            page_dir = tmp_path / name
            page_dir.mkdir()
            page_path = page_dir / "page.djx"
            page_path.write_text("")
            pages.append(page_path)

        discovery.discover_page_assets(pages[0], StaticCollector())
        discovery.discover_page_assets(pages[1], StaticCollector())
        discovery.discover_page_assets(pages[0], StaticCollector())
        discovery.discover_page_assets(pages[2], StaticCollector())

        assert list(discovery._page_plan_cache) == [pages[0], pages[2]]

    def test_a_nested_render_cannot_push_the_cache_past_its_limit(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        monkeypatch.setattr(discovery_mod, "_PAGE_PLAN_CACHE_MAX_SIZE", 1)
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        pages: list[Path] = []
        for name in ("outer", "inner"):
            page_dir = tmp_path / name
            page_dir.mkdir()
            (page_dir / "template.css").write_text("body{}")
            page_path = page_dir / "page.djx"
            page_path.write_text("")
            pages.append(page_path)
        nested: list[int] = []

        def render_the_other_page(sender, **kwargs) -> None:
            if not nested:
                nested.append(1)
                discovery.discover_page_assets(pages[1], StaticCollector())

        asset_registered.connect(render_the_other_page)
        try:
            discovery.discover_page_assets(pages[0], StaticCollector())
        finally:
            asset_registered.disconnect(render_the_other_page)

        assert nested == [1]
        assert len(discovery._page_plan_cache) == 1


class TestAssetDiscoveryWatchedDirectories:
    """A plan watches the directories it read, and stats them before reading."""

    @staticmethod
    def _watched(discovery: AssetDiscovery, page_path: Path) -> list[Path]:
        plan = discovery._page_plan_cache[page_path]
        return [directory for directory, _ in plan.directory_mtimes]

    def test_a_page_outside_every_tree_watches_only_its_own_directory(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_dir = tmp_path / "a" / "b" / "c"
        page_dir.mkdir(parents=True)
        page_path = page_dir / "page.djx"
        page_path.write_text("")
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        discovery.discover_page_assets(page_path, StaticCollector())
        assert self._watched(discovery, page_path) == [page_dir]

    def test_a_layout_above_an_untracked_page_is_watched(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        holder = tmp_path / "holder"
        page_dir = holder / "empty" / "leaf"
        page_dir.mkdir(parents=True)
        (holder / "layout.djx").write_text("")
        page_path = page_dir / "page.djx"
        page_path.write_text("")
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        discovery.discover_page_assets(page_path, StaticCollector())
        assert self._watched(discovery, page_path) == [holder, page_dir]

    def test_the_walk_stops_at_the_shared_ancestor_bound(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        current = tmp_path
        levels: list[Path] = []
        for depth in range(70):
            current = current / f"d{depth}"
            current.mkdir()
            levels.append(current)
        for depth in (5, 6):
            (levels[depth] / "layout.djx").write_text("")
            (levels[depth] / "layout.css").write_text("body{}")
        page_path = levels[-1] / "page.djx"
        page_path.write_text("")
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/d6/layout.css"
        ]

    def test_a_file_written_during_the_walk_shows_up_on_the_next_render(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        page_dir = tmp_path / "section"
        page_dir.mkdir()
        page_path = page_dir / "page.djx"
        page_path.write_text("")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)
        probe = discovery._find_role_files

        def _write_while_reading(
            directory: Path, *, logical_name: str, role: str
        ) -> list[object]:
            found = probe(directory, logical_name=logical_name, role=role)
            (page_dir / "template.css").write_text("body{}")
            return found

        with override_settings(DEBUG=True):
            monkeypatch.setattr(discovery, "_find_role_files", _write_while_reading)
            discovery.discover_page_assets(page_path, StaticCollector())
            monkeypatch.setattr(discovery, "_find_role_files", probe)
            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)

        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/section.css"
        ]


class _FailingBackend(StaticFilesBackend):
    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        msg = "cannot resolve"
        raise ValueError(msg)


class _ManifestBackend(RecordingStaticBackend):
    """Serves built URLs once a manifest lands, and the default URL until then.

    Models the manifest-driven backend the how-to guide walks through, whose
    answer changes the moment an asset build finishes under a running process.
    """

    def __init__(self, manifest: Path) -> None:
        """Bind the manifest path re-checked on every registration."""
        super().__init__()
        self._manifest = manifest

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the built URL when the manifest exists, else the default one."""
        if not self._manifest.exists():
            return super().register_file(source_path, logical_name, kind)
        self.calls.append((logical_name, kind))
        return f"/build/{logical_name}{default_kinds.extension(kind)}"


class _CssOnlyFailingBackend(RecordingStaticBackend):
    """Rejects `css` and serves everything else, for the partial-failure case."""

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        if kind == "css":
            msg = "cannot resolve"
            raise ValueError(msg)
        return super().register_file(source_path, logical_name, kind)


def _probe_counter(discovery: AssetDiscovery, monkeypatch) -> list[Path]:
    """Record every directory the stem probe reads."""
    probes: list[Path] = []
    walk_disk = discovery._find_role_files

    def _record(directory: Path, *, logical_name: str, role: str) -> list[object]:
        probes.append(directory)
        return walk_disk(directory, logical_name=logical_name, role=role)

    monkeypatch.setattr(discovery, "_find_role_files", _record)
    return probes


def _module_list_counter(discovery: AssetDiscovery, monkeypatch) -> list[Path]:
    """Record every module the `styles` and `scripts` reader is pointed at."""
    reads: list[Path] = []
    read_lists = discovery._module_lists

    def _record(module_path: Path) -> dict[str, list[str]]:
        reads.append(module_path)
        return read_lists(module_path)

    monkeypatch.setattr(discovery, "_module_lists", _record)
    return reads


def _asset_fields(collector: StaticCollector) -> list[tuple[str, str, str, str | None]]:
    """Return every collected asset as a comparable tuple, in slot order."""
    return [
        (slot, a.url, a.kind, str(a.source_path) if a.source_path else None)
        for slot in ("styles", "scripts")
        for a in collector.assets_in_slot(slot)
    ]


def _tree_with_every_asset_shape(tmp_path: Path) -> Path:
    """Build two layout levels, a template pair, and a page with module lists."""
    (tmp_path / "layout.djx").write_text("")
    (tmp_path / "layout.css").write_text("body{}")
    middle = tmp_path / "middle"
    middle.mkdir()
    (middle / "layout.djx").write_text("")
    (middle / "layout.js").write_text("/* js */")
    page_dir = middle / "post"
    page_dir.mkdir()
    (page_dir / "template.css").write_text("body{}")
    (page_dir / "template.js").write_text("/* js */")
    page_path = page_dir / "page.py"
    page_path.write_text(
        'styles = ["https://cdn.example.com/x.css"]\n'
        'scripts = ["https://cdn.example.com/x.js"]\n'
    )
    return page_path


def _bump(path: Path) -> None:
    """Push a path's mtime forward so a plan built from it goes stale."""
    moved = path.stat().st_mtime + 10
    os.utime(path, (moved, moved))


def _rewind(path: Path) -> None:
    """Pull a path's mtime back, the way restoring a backup over it would."""
    moved = path.stat().st_mtime - 10
    os.utime(path, (moved, moved))


class TestAssetDiscoveryPagePlanWarmRender:
    """A warm page render keeps the disk facts and asks the backend again."""

    def test_a_warm_render_matches_the_first_pass_asset_for_asset(
        self, tmp_path: Path
    ) -> None:
        page_path = _tree_with_every_asset_shape(tmp_path)
        discovery = AssetDiscovery(
            StaticAssetProvider(RecordingStaticBackend(), (tmp_path.resolve(),))
        )

        cold = StaticCollector()
        discovery.discover_page_assets(page_path, cold)
        warm = StaticCollector()
        discovery.discover_page_assets(page_path, warm)

        assert _asset_fields(warm) == _asset_fields(cold)
        assert _asset_fields(cold) != []

    def test_a_warm_render_reuses_the_module_url_assets(self, tmp_path: Path) -> None:
        page_path = _tree_with_every_asset_shape(tmp_path)
        discovery = AssetDiscovery(
            StaticAssetProvider(RecordingStaticBackend(), (tmp_path.resolve(),))
        )

        cold = StaticCollector()
        discovery.discover_page_assets(page_path, cold)
        warm = StaticCollector()
        discovery.discover_page_assets(page_path, warm)

        cold_urls = [a for a in cold.assets_in_slot("styles") if not a.source_path]
        warm_urls = [a for a in warm.assets_in_slot("styles") if not a.source_path]
        assert [id(a) for a in warm_urls] == [id(a) for a in cold_urls]
        assert cold_urls != []

    def test_a_warm_render_registers_every_found_file_again(
        self, tmp_path: Path
    ) -> None:
        page_path = _tree_with_every_asset_shape(tmp_path)
        backend = RecordingStaticBackend()
        discovery = AssetDiscovery(StaticAssetProvider(backend, (tmp_path.resolve(),)))
        discovery.discover_page_assets(page_path, StaticCollector())
        cold_calls = list(backend.calls)

        backend.calls.clear()
        discovery.discover_page_assets(page_path, StaticCollector())
        assert len(cold_calls) == 4
        assert backend.calls == cold_calls

    def test_a_backend_answering_differently_is_seen_next_render(
        self, tmp_path: Path
    ) -> None:
        """The manifest recipe from the docs, where a build lands mid-process."""
        page_path = _tree_with_every_asset_shape(tmp_path)
        manifest = tmp_path / "manifest.json"
        backend = _ManifestBackend(manifest)
        discovery = AssetDiscovery(StaticAssetProvider(backend, (tmp_path.resolve(),)))

        fallback = StaticCollector()
        discovery.discover_page_assets(page_path, fallback)
        manifest.write_text("{}")
        built = StaticCollector()
        discovery.discover_page_assets(page_path, built)

        assert [a.url for a in fallback.assets_in_slot("styles")] == [
            "/static/next/layout.css",
            "/static/next/middle/post.css",
            "https://cdn.example.com/x.css",
        ]
        assert [a.url for a in built.assets_in_slot("styles")] == [
            "/build/layout.css",
            "/build/middle/post.css",
            "https://cdn.example.com/x.css",
        ]

    def test_a_warm_render_reads_neither_the_folders_nor_the_module(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        page_path = _tree_with_every_asset_shape(tmp_path)
        discovery = AssetDiscovery(
            StaticAssetProvider(RecordingStaticBackend(), (tmp_path.resolve(),))
        )
        probes = _probe_counter(discovery, monkeypatch)
        reads = _module_list_counter(discovery, monkeypatch)

        discovery.discover_page_assets(page_path, StaticCollector())
        cold_probes, cold_reads = list(probes), list(reads)
        discovery.discover_page_assets(page_path, StaticCollector())

        assert len(cold_probes) == 3
        assert len(cold_reads) == 1
        assert probes == cold_probes
        assert reads == cold_reads

    def test_off_debug_a_warm_render_stats_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        page_path = _tree_with_every_asset_shape(tmp_path)
        discovery = AssetDiscovery(
            StaticAssetProvider(RecordingStaticBackend(), (tmp_path.resolve(),))
        )
        discovery.discover_page_assets(page_path, StaticCollector())

        stats: list[Path] = []
        probes: list[Path] = []
        real_stat = Path.stat
        real_exists = Path.exists

        def _record(self: Path, **kwargs) -> os.stat_result:
            stats.append(self)
            return real_stat(self, **kwargs)

        def _record_exists(self: Path, **kwargs) -> bool:
            probes.append(self)
            return real_exists(self, **kwargs)

        monkeypatch.setattr(Path, "stat", _record)
        monkeypatch.setattr(Path, "exists", _record_exists)
        with override_settings(DEBUG=False):
            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert stats == []
        assert probes == []
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/layout.css",
            "/static/next/middle/post.css",
            "https://cdn.example.com/x.css",
        ]

    def test_debug_picks_up_a_layout_added_in_a_walked_directory(
        self, tmp_path: Path
    ) -> None:
        """A `layout.djx` created between root and page is visible next request."""
        page_path = _tree_with_every_asset_shape(tmp_path)
        (tmp_path / "middle" / "layout.djx").unlink()
        (tmp_path / "middle" / "layout.js").unlink()
        discovery = AssetDiscovery(
            StaticAssetProvider(RecordingStaticBackend(), (tmp_path.resolve(),))
        )
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())

            (tmp_path / "middle" / "layout.djx").write_text("")
            (tmp_path / "middle" / "layout.css").write_text("body{}")
            _bump(tmp_path / "middle")

            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/layout.css",
            "/static/next/middle/layout.css",
            "/static/next/middle/post.css",
            "https://cdn.example.com/x.css",
        ]

    def test_debug_drops_an_asset_deleted_between_renders(self, tmp_path: Path) -> None:
        page_path = _tree_with_every_asset_shape(tmp_path)
        discovery = AssetDiscovery(
            StaticAssetProvider(RecordingStaticBackend(), (tmp_path.resolve(),))
        )
        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())

            (page_path.parent / "template.css").unlink()
            _bump(page_path.parent)

            collector = StaticCollector()
            discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/layout.css",
            "https://cdn.example.com/x.css",
        ]


class TestAssetDiscoveryPlanFreshness:
    """A plan is born describing the disk the pass that built it left behind."""

    def test_importing_the_page_module_does_not_age_its_own_plan(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """The bytecode the import writes lands before the plan takes an mtime."""
        page_dir = tmp_path / "section"
        page_dir.mkdir()
        (page_dir / "template.css").write_text("body{}")
        page_path = page_dir / "page.py"
        page_path.write_text('styles = ["https://cdn.example.com/x.css"]\n')
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        with override_settings(DEBUG=True):
            discovery.discover_page_assets(page_path, StaticCollector())
            plan = discovery._page_plan_cache[page_path]

        assert (page_dir / "__pycache__").exists()
        assert plan.module_assets != ()
        with override_settings(DEBUG=True):
            assert discovery._plan_stale(plan) is False

    def test_importing_a_component_module_does_not_age_its_own_plan(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        module_path = directory / "component.py"
        module_path.write_text('styles = ["https://cdn.example.com/w.css"]\n')
        info = _component_at(directory, "widget", module=module_path)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        with override_settings(DEBUG=True):
            discovery.discover_component_assets(info, StaticCollector())
            plan = discovery._component_plan_cache[_component_key(info)]

            assert (directory / "__pycache__").exists()
            assert discovery._plan_stale(plan) is False


class TestAssetDiscoveryRegistryGeneration:
    """A registration moves no file, so a plan tracks the registries it read."""

    def test_a_stem_registered_later_reaches_a_planned_page(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """Teaching discovery a new stem takes effect with `DEBUG` off too."""
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        (tmp_path / "hero.css").write_text("body{}")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        cold = StaticCollector()
        discovery.discover_page_assets(page_path, cold)
        with restored_static_registries():
            default_stems.register("template", "hero")
            warm = StaticCollector()
            discovery.discover_page_assets(page_path, warm)

        assert cold.assets_in_slot("styles") == []
        assert [a.url for a in warm.assets_in_slot("styles")] == [
            "/static/next/index.css"
        ]

    def test_a_kind_registered_later_reaches_a_planned_component(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A folder file of an unknown extension is no asset until the kind is."""
        directory = tmp_path / "widget"
        directory.mkdir()
        (directory / "component.ts").write_text("export default 1")
        info = _component_at(directory, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        cold = StaticCollector()
        discovery.discover_component_assets(info, cold)
        with restored_static_registries():
            default_kinds.register(
                "ts", extension=".ts", slot="scripts", renderer="render_module_tag"
            )
            warm = StaticCollector()
            discovery.discover_component_assets(info, warm)

        assert cold.assets_in_slot("scripts") == []
        assert [a.url for a in warm.assets_in_slot("scripts")] == [
            "/static/next/components/widget.ts"
        ]

    def test_a_slot_registered_later_reaches_a_planned_page(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A module list is named after a slot, so a new one opens a new list."""
        page_dir = tmp_path / "hero"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('preloads = ["https://cdn.example.com/hero.avif"]\n')
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        cold = StaticCollector()
        discovery.discover_page_assets(page_path, cold)
        with restored_static_registries():
            default_placeholders.register("preloads", token="<!-- next:preloads -->")
            default_kinds.register(
                "avif", extension=".avif", slot="preloads", renderer="render_link_tag"
            )
            warm = StaticCollector()
            discovery.discover_page_assets(page_path, warm)

        assert cold.assets_in_slot("preloads") == []
        assert [a.url for a in warm.assets_in_slot("preloads")] == [
            "https://cdn.example.com/hero.avif"
        ]

    def test_the_generation_reads_the_stem_registry_it_was_handed(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A caller handing over a stem registry gets plans that track that one."""
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        (tmp_path / "hero.css").write_text("body{}")
        stems = StemRegistry()
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider, stems=stems)

        cold = StaticCollector()
        discovery.discover_page_assets(page_path, cold)
        stems.register("template", "hero")
        warm = StaticCollector()
        discovery.discover_page_assets(page_path, warm)

        assert cold.assets_in_slot("styles") == []
        assert [a.url for a in warm.assets_in_slot("styles")] == [
            "/static/next/index.css"
        ]

    def test_a_stem_registered_during_the_page_probe_is_read_next_render(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        """The generation is read before the probe, as the mtime snapshot is.

        A registration landing while the plan is built would otherwise be baked
        into it as already seen, and the file it names would stay invisible.
        """
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        (tmp_path / "hero.css").write_text("body{}")
        stems = StemRegistry()
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider, stems=stems)
        probe = discovery._find_role_files

        def _register_while_reading(
            directory: Path, *, logical_name: str, role: str
        ) -> list[object]:
            found = probe(directory, logical_name=logical_name, role=role)
            stems.register("template", "hero")
            return found

        monkeypatch.setattr(discovery, "_find_role_files", _register_while_reading)
        discovery.discover_page_assets(page_path, StaticCollector())

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/index.css"
        ]

    def test_a_stem_registered_during_the_component_probe_is_read_next_render(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        """The component builder reads the generation before the probe too."""
        directory = tmp_path / "widget"
        directory.mkdir()
        (directory / "hero.css").write_text(".w{}")
        info = _component_at(directory, "widget", module=None)
        stems = StemRegistry()
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()), stems=stems)
        probe = discovery._find_role_files

        def _register_while_reading(
            folder: Path, *, logical_name: str, role: str
        ) -> list[object]:
            found = probe(folder, logical_name=logical_name, role=role)
            stems.register("component", "hero")
            return found

        monkeypatch.setattr(discovery, "_find_role_files", _register_while_reading)
        discovery.discover_component_assets(info, StaticCollector())

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]


class TestRestoredStaticRegistries:
    """The scaffolding puts the state back without putting the counter back."""

    def test_two_blocks_never_share_a_generation(
        self, file_backend: StaticBackend
    ) -> None:
        """A version rolled backwards would let a genuinely stale plan read fresh."""
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        with restored_static_registries():
            default_stems.register("template", "hero")
            first = discovery._registry_generation()
        with restored_static_registries():
            default_stems.register("template", "gallery")
            second = discovery._registry_generation()

        assert first != second
        assert default_stems.stems("template") == ("template",)


class TestAssetDiscoveryPagePlanFailures:
    """A file the backend rejects is offered to it again on the next render."""

    def test_the_warning_repeats_on_every_render(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        discovery = AssetDiscovery(
            StaticAssetProvider(_FailingBackend(), (tmp_path.resolve(),))
        )

        with caplog.at_level("WARNING", logger="next.static.discovery"):
            discovery.discover_page_assets(page_path, StaticCollector())
            discovery.discover_page_assets(page_path, StaticCollector())

        failures = [r for r in caplog.records if "Failed to register" in r.getMessage()]
        assert len(failures) == 2

    def test_a_partial_failure_still_collects_what_registered(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "template.css").write_text("")
        (tmp_path / "template.js").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        discovery = AssetDiscovery(
            StaticAssetProvider(_CssOnlyFailingBackend(), (tmp_path.resolve(),))
        )

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("styles") == []
        assert [a.url for a in collector.assets_in_slot("scripts")] == [
            "/static/next/index.js"
        ]


class TestAssetDiscoverySourcePaths:
    """Collected assets carry an absolute on-disk path with no extra resolution."""

    def test_page_asset_carries_an_absolute_existing_path(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        css = tmp_path / "template.css"
        css.write_text("body{}")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        (asset,) = collector.assets_in_slot("styles")
        assert asset.source_path is not None
        assert asset.source_path.is_absolute()
        assert asset.source_path.exists()
        assert asset.source_path == css.resolve()

    def test_component_asset_carries_an_absolute_existing_path(
        self, file_backend: StaticBackend, composite_component: ComponentInfo
    ) -> None:
        provider = StaticAssetProvider(file_backend, ())
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_component_assets(composite_component, collector)
        assert composite_component.template_path is not None
        component_dir = composite_component.template_path.parent
        sources = [
            a.source_path
            for a in collector.assets_in_slot("styles")
            if a.source_path is not None
        ]
        assert sources == [component_dir / "component.css"]
        assert sources[0].is_absolute()
        assert sources[0].exists()

    def test_component_asset_resolves_the_folder_it_was_reached_through(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        real_dir = tmp_path / "widget"
        real_dir.mkdir()
        (real_dir / "component.djx").write_text("<div/>")
        (real_dir / "component.css").write_text(".w{}")
        linked_dir = tmp_path / "linked"
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        info = ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=linked_dir / "component.djx",
            module_path=None,
            is_simple=False,
        )
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        assert [a.source_path for a in collector.assets_in_slot("styles")] == [
            real_dir / "component.css"
        ]

    def test_a_symlinked_asset_file_reports_its_target(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """The staticfiles finder spells a hit resolved, and the two have to agree."""
        shared = tmp_path / "shared"
        shared.mkdir()
        target = shared / "theme.css"
        target.write_text(".shared{}")
        directory = tmp_path / "widget"
        directory.mkdir()
        (directory / "component.css").symlink_to(target)
        info = _component_at(directory, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        assert [a.source_path for a in collector.assets_in_slot("styles")] == [target]

    def test_a_symlinked_page_tree_yields_the_same_urls_and_sources(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        real_root = tmp_path / "real"
        page_dir = real_root / "section"
        page_dir.mkdir(parents=True)
        (real_root / "layout.djx").write_text("")
        (real_root / "layout.css").write_text("")
        (page_dir / "template.css").write_text("body{}")
        (page_dir / "page.djx").write_text("")
        linked_root = tmp_path / "link"
        linked_root.symlink_to(real_root, target_is_directory=True)

        provider = StaticAssetProvider(file_backend, (real_root.resolve(),))
        discovery = AssetDiscovery(provider)

        direct = StaticCollector()
        discovery.discover_page_assets(page_dir / "page.djx", direct)
        through_link = StaticCollector()
        discovery.discover_page_assets(
            linked_root / "section" / "page.djx", through_link
        )

        assert [a.url for a in through_link.assets_in_slot("styles")] == [
            a.url for a in direct.assets_in_slot("styles")
        ]
        assert [a.source_path for a in through_link.assets_in_slot("styles")] == [
            a.source_path for a in direct.assets_in_slot("styles")
        ]

    def test_content_dedup_holds_across_path_spellings(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        real_dir = tmp_path / "widget"
        real_dir.mkdir()
        (real_dir / "component.djx").write_text("<div/>")
        (real_dir / "component.css").write_text(".shared {}")
        linked_dir = tmp_path / "linked"
        linked_dir.symlink_to(real_dir, target_is_directory=True)

        provider = StaticAssetProvider(file_backend, ())
        discovery = AssetDiscovery(provider)
        dedup = HashContentDedup()
        collector = StaticCollector(dedup=dedup)
        for name, directory in (("widget", real_dir), ("mirror", linked_dir)):
            discovery.discover_component_assets(
                ComponentInfo(
                    name=name,
                    scope_root=tmp_path,
                    scope_relative="",
                    template_path=directory / "component.djx",
                    module_path=None,
                    is_simple=False,
                ),
                collector,
            )

        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]
        assert len(dedup._cache) == 1


class TestAssetDiscoveryModuleListUrlRouting:
    """`scripts`/`styles` list URLs are dropped when their suffix mismatches."""

    def test_url_without_extension_is_dropped(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_dir = tmp_path / "noext"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('scripts = ["https://cdn.example.com/loader"]\n')

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("scripts") == []

    def test_url_with_unregistered_extension_is_dropped(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        page_dir = tmp_path / "weird"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('scripts = ["https://cdn.example.com/asset.zzz"]\n')

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("scripts") == []

    def test_url_with_mismatched_slot_is_dropped(
        self,
        tmp_path: Path,
        file_backend: StaticBackend,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        page_dir = tmp_path / "mismatch"
        page_dir.mkdir()
        page_path = page_dir / "page.py"
        page_path.write_text('scripts = ["https://cdn.example.com/styling.css"]\n')

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        with caplog.at_level("DEBUG", logger="next.static.discovery"):
            discovery.discover_page_assets(page_path, collector)
        assert collector.assets_in_slot("scripts") == []
        assert collector.assets_in_slot("styles") == []

        message = next(
            r.getMessage() for r in caplog.records if "styling.css" in r.getMessage()
        )
        assert message == (
            "Module URL 'https://cdn.example.com/styling.css' maps to kind 'css' "
            "in slot 'styles', so the 'scripts' list dropped it"
        )


class TestAssetDiscoveryComponents:
    def test_simple_component_yields_nothing(
        self, file_backend: StaticBackend, simple_component: ComponentInfo
    ) -> None:
        provider = StaticAssetProvider(file_backend, ())
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_component_assets(simple_component, collector)
        assert collector.assets_in_slot("styles") == []
        assert collector.assets_in_slot("scripts") == []

    def test_composite_component_picks_up_css_js_and_module_lists(
        self, file_backend: StaticBackend, composite_component: ComponentInfo
    ) -> None:
        provider = StaticAssetProvider(file_backend, ())
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        discovery.discover_component_assets(composite_component, collector)

        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        script_urls = [a.url for a in collector.assets_in_slot("scripts")]
        assert "/static/next/components/widget.css" in style_urls
        assert "/static/next/components/widget.js" in script_urls
        assert "https://cdn.example.com/extra.css" in style_urls
        assert "https://cdn.example.com/extra.js" in script_urls


def _component_key(info: ComponentInfo) -> tuple[Path | None, Path | None, str]:
    """Return the cache key the discovery layer files a component plan under."""
    return (info.template_path, info.module_path, info.name)


def _component_at(directory: Path, name: str, *, module: Path | None) -> ComponentInfo:
    """Return a composite component whose folder holds a written template."""
    return component_info(
        directory, name=name, module=module, template=f"<div>{name}</div>"
    )


class TestAssetDiscoveryComponentPlanCache:
    """Every extra instance of a component reuses the folder walk of the first."""

    def test_fifty_instances_probe_the_folder_once(
        self,
        file_backend: StaticBackend,
        composite_component: ComponentInfo,
        monkeypatch,
    ) -> None:
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        probes = _probe_counter(discovery, monkeypatch)

        collector = StaticCollector()
        for _ in range(50):
            discovery.discover_component_assets(composite_component, collector)

        assert len(probes) == 1
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css",
            "https://cdn.example.com/extra.css",
        ]

    def test_a_warm_instance_registers_every_found_file_again(
        self, composite_component: ComponentInfo
    ) -> None:
        backend = RecordingStaticBackend()
        discovery = AssetDiscovery(StaticAssetProvider(backend, ()))
        discovery.discover_component_assets(composite_component, StaticCollector())
        cold_calls = list(backend.calls)

        backend.calls.clear()
        discovery.discover_component_assets(composite_component, StaticCollector())
        assert len(cold_calls) == 2
        assert backend.calls == cold_calls

    def test_fifty_instances_register_every_found_file_each_time(
        self, composite_component: ComponentInfo
    ) -> None:
        backend = RecordingStaticBackend()
        discovery = AssetDiscovery(StaticAssetProvider(backend, ()))

        collector = StaticCollector()
        for _ in range(50):
            discovery.discover_component_assets(composite_component, collector)

        assert len(backend.calls) == 100

    def test_an_equal_info_from_a_rescan_hits_the_same_entry(
        self, composite_component: ComponentInfo, monkeypatch
    ) -> None:
        """A rescan rebuilding the same component reuses the plan it keyed."""
        discovery = AssetDiscovery(StaticAssetProvider(RecordingStaticBackend(), ()))
        discovery.discover_component_assets(composite_component, StaticCollector())
        probes = _probe_counter(discovery, monkeypatch)

        rescanned = ComponentInfo(
            name=composite_component.name,
            scope_root=composite_component.scope_root,
            scope_relative=composite_component.scope_relative,
            template_path=composite_component.template_path,
            module_path=composite_component.module_path,
            is_simple=False,
        )
        collector = StaticCollector()
        discovery.discover_component_assets(rescanned, collector)

        assert probes == []
        assert len(discovery._component_plan_cache) == 1
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css",
            "https://cdn.example.com/extra.css",
        ]

    def test_a_renamed_component_keys_its_own_entry(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        (directory / "component.css").write_text(".w{}")
        first = _component_at(directory, "widget", module=None)
        renamed = ComponentInfo(
            name="gadget",
            scope_root=first.scope_root,
            scope_relative="",
            template_path=first.template_path,
            module_path=None,
            is_simple=False,
        )
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        collector = StaticCollector()
        discovery.discover_component_assets(first, collector)
        discovery.discover_component_assets(renamed, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css",
            "/static/next/components/gadget.css",
        ]

    def test_debug_notices_a_stylesheet_added_next_to_the_component(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        info = _component_at(directory, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        with override_settings(DEBUG=True):
            discovery.discover_component_assets(info, StaticCollector())

            (directory / "component.css").write_text(".w{}")
            _bump(directory)

            collector = StaticCollector()
            discovery.discover_component_assets(info, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]

    def test_off_debug_a_stylesheet_added_later_stays_invisible(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        info = _component_at(directory, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        discovery.discover_component_assets(info, StaticCollector())

        (directory / "component.css").write_text(".w{}")
        _bump(directory)

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        assert collector.assets_in_slot("styles") == []

    def test_a_module_outside_the_component_folder_is_watched_too(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        elsewhere = tmp_path / "lists"
        elsewhere.mkdir()
        module_path = elsewhere / "component.py"
        module_path.write_text('styles = ["https://cdn.example.com/from-away.css"]\n')
        info = _component_at(directory, "widget", module=module_path)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        plan = discovery._component_plan_cache[_component_key(info)]
        assert [d for d, _ in plan.directory_mtimes] == [directory, elsewhere]
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "https://cdn.example.com/from-away.css"
        ]

    def test_a_module_that_is_not_on_disk_is_skipped(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        info = _component_at(directory, "widget", module=directory / "component.py")
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)
        assert collector.assets_in_slot("styles") == []
        key = _component_key(info)
        assert discovery._component_plan_cache[key].module_assets == ()

    def test_a_folder_that_never_existed_leaves_the_plan_alone(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A module folder that was missing at build time is no reason to rebuild."""
        directory = tmp_path / "widget"
        directory.mkdir()
        info = _component_at(directory, "widget", module=tmp_path / "gone" / "lists.py")
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        with override_settings(DEBUG=True):
            discovery.discover_component_assets(info, StaticCollector())
            plan = discovery._component_plan_cache[_component_key(info)]
            assert discovery._plan_stale(plan) is False

            (tmp_path / "gone").mkdir()
            assert discovery._plan_stale(plan) is True

    def test_a_folder_that_goes_away_makes_the_plan_stale(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """A directory the plan read and can no longer stat rebuilds it."""
        directory = tmp_path / "widget"
        directory.mkdir()
        info = _component_at(directory, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        with override_settings(DEBUG=True):
            discovery.discover_component_assets(info, StaticCollector())
            plan = discovery._component_plan_cache[_component_key(info)]
            shutil.rmtree(directory)
            assert discovery._plan_stale(plan) is True

    def test_the_plan_cache_evicts_the_oldest_key(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        monkeypatch.setattr(discovery_mod, "_COMPONENT_PLAN_CACHE_MAX_SIZE", 1)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        for i in range(2):
            directory = tmp_path / f"widget_{i}"
            directory.mkdir()
            info = _component_at(directory, f"widget_{i}", module=None)
            discovery.discover_component_assets(info, StaticCollector())
        assert len(discovery._component_plan_cache) <= 1

    def test_a_warm_hit_keeps_a_full_cache_in_use_order(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        """A component mounted again is the freshest entry, not the next evicted."""
        monkeypatch.setattr(discovery_mod, "_COMPONENT_PLAN_CACHE_MAX_SIZE", 2)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        infos = []
        for name in ("first", "second", "third"):
            directory = tmp_path / name
            directory.mkdir()
            infos.append(_component_at(directory, name, module=None))

        discovery.discover_component_assets(infos[0], StaticCollector())
        discovery.discover_component_assets(infos[1], StaticCollector())
        discovery.discover_component_assets(infos[0], StaticCollector())
        discovery.discover_component_assets(infos[2], StaticCollector())

        assert list(discovery._component_plan_cache) == [
            _component_key(infos[0]),
            _component_key(infos[2]),
        ]

    def test_a_simple_component_is_never_planned(
        self, file_backend: StaticBackend, simple_component: ComponentInfo
    ) -> None:
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        collector = StaticCollector()
        discovery.discover_component_assets(simple_component, collector)
        discovery.discover_component_assets(simple_component, collector)
        assert collector.assets_in_slot("styles") == []
        assert discovery._component_plan_cache == {}


class TestAssetDiscoveryComponentPlanFolders:
    """A component plan tracks the folder it read, however it spells it."""

    def test_a_folder_that_does_not_stat_is_recorded_as_nothing(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        """Nothing rather than a sentinel a pre-epoch mtime could collide with."""
        directory = tmp_path / "widget"
        info = component_info(directory)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        discovery.discover_component_assets(info, StaticCollector())

        plan = discovery._component_plan_cache[_component_key(info)]
        assert plan.directory_mtimes == ((directory.resolve(), None),)

    def test_a_component_without_a_template_resolves_its_folder_once(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        """With no template the folder comes from the module path, derived once."""
        directory = tmp_path / "widget"
        directory.mkdir()
        module_path = directory / "component.py"
        module_path.write_text("")
        (directory / "component.css").write_text(".w{}")
        info = ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=None,
            module_path=module_path,
            is_simple=False,
        )
        resolved = directory.resolve()
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        seen = record_path_calls(monkeypatch, "resolve", keep=lambda p: p == directory)

        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)

        plan = discovery._component_plan_cache[_component_key(info)]
        assert seen == [directory]
        assert [folder for folder, _ in plan.directory_mtimes] == [resolved]
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]

    def test_a_plan_read_over_a_missing_folder_rebuilds_when_it_appears(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        info = component_info(directory)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        with override_settings(DEBUG=True):
            discovery.discover_component_assets(info, StaticCollector())

            directory.mkdir()
            (directory / "component.css").write_text(".w{}")

            collector = StaticCollector()
            discovery.discover_component_assets(info, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]

    def test_a_folder_that_will_not_resolve_keeps_its_own_spelling(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        """A folder that will not resolve is read under the name it was reached by.

        A render is no place to raise over the spelling of a directory.
        """
        real_dir = tmp_path / "widget"
        real_dir.mkdir()
        (real_dir / "component.css").write_text(".w{}")
        linked_dir = tmp_path / "linked"
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        info = _component_at(linked_dir, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        resolve = Path.resolve

        def _refuse_the_folder(self: Path, **kwargs) -> Path:
            if self == linked_dir:
                msg = "the working directory is gone"
                raise OSError(msg)
            return resolve(self, **kwargs)

        monkeypatch.setattr(Path, "resolve", _refuse_the_folder)
        collector = StaticCollector()
        discovery.discover_component_assets(info, collector)

        plan = discovery._component_plan_cache[_component_key(info)]
        assert [directory for directory, _ in plan.directory_mtimes] == [linked_dir]
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]

    def test_a_file_written_during_the_probe_shows_up_on_the_next_render(
        self, tmp_path: Path, file_backend: StaticBackend, monkeypatch
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        info = _component_at(directory, "widget", module=None)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))
        probe = discovery._find_role_files

        def _write_while_reading(
            folder: Path, *, logical_name: str, role: str
        ) -> list[object]:
            found = probe(folder, logical_name=logical_name, role=role)
            (directory / "component.css").write_text(".w{}")
            return found

        with override_settings(DEBUG=True):
            monkeypatch.setattr(discovery, "_find_role_files", _write_while_reading)
            discovery.discover_component_assets(info, StaticCollector())
            monkeypatch.setattr(discovery, "_find_role_files", probe)
            collector = StaticCollector()
            discovery.discover_component_assets(info, collector)

        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]

    def test_a_simple_info_does_not_read_the_composite_plan(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        (directory / "component.css").write_text(".w{}")
        composite = _component_at(directory, "widget", module=None)
        simple = component_info(directory, is_simple=True)
        discovery = AssetDiscovery(StaticAssetProvider(file_backend, ()))

        composite_collector = StaticCollector()
        discovery.discover_component_assets(composite, composite_collector)
        simple_collector = StaticCollector()
        discovery.discover_component_assets(simple, simple_collector)

        assert [a.url for a in composite_collector.assets_in_slot("styles")] == [
            "/static/next/components/widget.css"
        ]
        assert simple_collector.assets_in_slot("styles") == []
        assert list(discovery._component_plan_cache) == [_component_key(composite)]


class TestAssetDiscoveryErrorHandling:
    def test_warning_logged_on_value_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")

        provider = StaticAssetProvider(_FailingBackend(), (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider)

        collector = StaticCollector()
        with caplog.at_level("WARNING", logger="next.static.discovery"):
            discovery.discover_page_assets(page_path, collector)

        assert collector.assets_in_slot("styles") == []
        assert any(
            "Failed to register static asset" in r.getMessage() for r in caplog.records
        )


class TestAssetDiscoveryCustomStems:
    def test_custom_template_stem_is_picked_up(
        self, tmp_path: Path, file_backend: StaticBackend
    ) -> None:
        stems = StemRegistry()
        stems.register("template", "page")
        (tmp_path / "page.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")

        provider = StaticAssetProvider(file_backend, (tmp_path.resolve(),))
        discovery = AssetDiscovery(provider, stems=stems)

        collector = StaticCollector()
        discovery.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/index.css"
        ]
