from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from next.components import ComponentInfo
from next.static import (
    SCRIPTS_PLACEHOLDER,
    STYLES_PLACEHOLDER,
    AssetDiscovery,
    FileStaticBackend,
    StaticAsset,
    StaticBackend,
    StaticCollector,
    StaticManager,
    StaticsFactory,
    _FileRegistryEntry,
    _kind_to_extension,
    _load_python_module,
    _read_string_list,
    static_manager,
    static_serve_view,
)


if TYPE_CHECKING:
    from collections.abc import Generator


CSS_URL = "https://example.com/a.css"
JS_URL = "https://example.com/a.js"


@pytest.fixture()
def fresh_manager() -> StaticManager:
    """Return a freshly instantiated ``StaticManager`` with no loaded backends."""
    return StaticManager()


@pytest.fixture()
def collector() -> StaticCollector:
    """Return a fresh ``StaticCollector`` for each test."""
    return StaticCollector()


@pytest.fixture()
def file_backend() -> FileStaticBackend:
    """Return a default ``FileStaticBackend`` with an empty registry."""
    return FileStaticBackend()


@pytest.fixture()
def _reset_global_static_manager() -> Generator[None, None, None]:
    """Reload the global ``static_manager`` backends after each test that mutates it."""
    yield
    static_manager._backends.clear()
    static_manager._discovery = None
    static_manager._cached_page_roots = None


@pytest.fixture()
def simple_component(tmp_path: Path) -> ComponentInfo:
    """Return a ``ComponentInfo`` pointing to a simple ``.djx`` template file."""
    template_path = tmp_path / "card.djx"
    template_path.write_text("<div>card</div>")
    return ComponentInfo(
        name="card",
        scope_root=tmp_path,
        scope_relative="",
        template_path=template_path,
        module_path=None,
        is_simple=True,
    )


@pytest.fixture()
def composite_component(tmp_path: Path) -> ComponentInfo:
    """Return a composite ``ComponentInfo`` with co-located css/js/py siblings."""
    comp_dir = tmp_path / "_components" / "widget"
    comp_dir.mkdir(parents=True)
    template_path = comp_dir / "component.djx"
    template_path.write_text("<div>widget</div>")
    module_path = comp_dir / "component.py"
    module_path.write_text(
        'styles = ["https://cdn.example.com/extra.css"]\n'
        'scripts = ["https://cdn.example.com/extra.js"]\n'
    )
    (comp_dir / "component.css").write_text(".widget {}")
    (comp_dir / "component.js").write_text("/* widget */")
    return ComponentInfo(
        name="widget",
        scope_root=tmp_path,
        scope_relative="",
        template_path=template_path,
        module_path=module_path,
        is_simple=False,
    )


class TestStaticAsset:
    """``StaticAsset`` is a slotted, frozen dataclass with an optional source path."""

    def test_defaults(self) -> None:
        """Only ``url`` and ``kind`` are required, ``source_path`` defaults to ``None``."""
        asset = StaticAsset(url=CSS_URL, kind="css")
        assert asset.url == CSS_URL
        assert asset.kind == "css"
        assert asset.source_path is None

    def test_explicit_source_path(self, tmp_path: Path) -> None:
        """``source_path`` can carry the origin path for file-backed assets."""
        asset = StaticAsset(url=CSS_URL, kind="css", source_path=tmp_path)
        assert asset.source_path == tmp_path


class TestStaticCollector:
    """The collector dedupes by URL and splits assets by kind in insertion order."""

    def test_css_and_js_go_to_separate_lists(self, collector: StaticCollector) -> None:
        """CSS and JS assets are returned by dedicated accessors."""
        css = StaticAsset(url=CSS_URL, kind="css")
        js = StaticAsset(url=JS_URL, kind="js")
        collector.add(css)
        collector.add(js)
        assert collector.styles() == [css]
        assert collector.scripts() == [js]

    def test_duplicate_url_is_ignored(self, collector: StaticCollector) -> None:
        """A second ``add`` with the same URL does not grow any list."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert len(collector.styles()) == 1

    def test_unknown_kind_is_logged_and_dropped(
        self,
        collector: StaticCollector,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An asset whose ``kind`` is neither css nor js is ignored with a debug log."""
        with caplog.at_level("DEBUG", logger="next.static"):
            collector.add(StaticAsset(url="weird://x", kind="weird"))
        assert collector.styles() == []
        assert collector.scripts() == []
        assert any("unknown kind" in rec.message for rec in caplog.records)

    def test_accessors_return_copies(self, collector: StaticCollector) -> None:
        """``styles`` and ``scripts`` return new lists so callers cannot mutate state."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        snapshot = collector.styles()
        snapshot.clear()
        assert collector.styles() == [StaticAsset(url=CSS_URL, kind="css")]

    def test_preserves_insertion_order(self, collector: StaticCollector) -> None:
        """Later unique URLs keep their relative order."""
        urls = [f"https://cdn/{i}.css" for i in range(3)]
        for url in urls:
            collector.add(StaticAsset(url=url, kind="css"))
        assert [asset.url for asset in collector.styles()] == urls

    def test_prepend_moves_asset_to_front(self, collector: StaticCollector) -> None:
        """``prepend=True`` inserts before previously appended items."""
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == ["/dep.css", "/own.css"]

    def test_prepend_preserves_order_across_multiple_calls(
        self, collector: StaticCollector
    ) -> None:
        """Sequential prepends keep their registration order relative to each other."""
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/a.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/b.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/c.css", kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == [
            "/a.css",
            "/b.css",
            "/c.css",
            "/own.css",
        ]

    def test_prepend_works_for_scripts(self, collector: StaticCollector) -> None:
        """Prepend cursor is tracked independently for the scripts bucket."""
        collector.add(StaticAsset(url="/own.js", kind="js"))
        collector.add(StaticAsset(url="/a.js", kind="js"), prepend=True)
        collector.add(StaticAsset(url="/b.js", kind="js"), prepend=True)
        assert [a.url for a in collector.scripts()] == [
            "/a.js",
            "/b.js",
            "/own.js",
        ]

    def test_prepend_respects_dedup(self, collector: StaticCollector) -> None:
        """A URL already present is not re-prepended and the cursor does not move."""
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/dep2.css", kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == [
            "/dep.css",
            "/dep2.css",
            "/own.css",
        ]

    def test_dedup_across_append_then_prepend(self, collector: StaticCollector) -> None:
        """A URL first appended is ignored on a later ``prepend=True`` call."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == [CSS_URL]

    def test_dedup_across_prepend_then_append(self, collector: StaticCollector) -> None:
        """A URL first prepended is ignored on a later plain ``add`` call."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"), prepend=True)
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert [a.url for a in collector.styles()] == [CSS_URL]

    def test_dedup_is_cross_source_path(self, collector: StaticCollector) -> None:
        """Assets sharing a URL but different ``source_path`` values still dedup."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(
            StaticAsset(url=CSS_URL, kind="css", source_path=Path("/tmp/a.css"))
        )
        assert [a.url for a in collector.styles()] == [CSS_URL]

    def test_dedup_is_kind_scoped(self, collector: StaticCollector) -> None:
        """The seen-set is URL-keyed, so the same URL cannot live in both buckets."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="js"))
        assert [a.url for a in collector.styles()] == [CSS_URL]
        assert collector.scripts() == []


class TestStaticCollectorInline:
    """Inline assets from ``{% #use_style %}`` / ``{% #use_script %}`` blocks."""

    def test_inline_script_appended(self, collector: StaticCollector) -> None:
        """An inline JS asset lands in ``scripts`` with its body preserved."""
        collector.add(StaticAsset(url="", kind="js", inline="console.log(1)"))
        scripts = collector.scripts()
        assert len(scripts) == 1
        assert scripts[0].inline == "console.log(1)"
        assert scripts[0].url == ""

    def test_inline_style_appended(self, collector: StaticCollector) -> None:
        """An inline CSS asset lands in ``styles`` with its body preserved."""
        collector.add(StaticAsset(url="", kind="css", inline="body{color:red}"))
        styles = collector.styles()
        assert len(styles) == 1
        assert styles[0].inline == "body{color:red}"

    def test_identical_inline_bodies_dedupe(self, collector: StaticCollector) -> None:
        """Two inline assets with identical rendered bodies collapse to one entry."""
        collector.add(StaticAsset(url="", kind="js", inline="same()"))
        collector.add(StaticAsset(url="", kind="js", inline="same()"))
        assert len(collector.scripts()) == 1
        assert collector.scripts()[0].inline == "same()"

    def test_different_inline_bodies_kept_distinct(
        self, collector: StaticCollector
    ) -> None:
        """Inline assets with different rendered bodies are kept independently."""
        collector.add(StaticAsset(url="", kind="js", inline="one()"))
        collector.add(StaticAsset(url="", kind="js", inline="two()"))
        assert [a.inline for a in collector.scripts()] == ["one()", "two()"]

    def test_inline_dedup_is_kind_scoped(self, collector: StaticCollector) -> None:
        """Identical bodies in different kinds live in both buckets independently."""
        collector.add(StaticAsset(url="", kind="css", inline="same"))
        collector.add(StaticAsset(url="", kind="js", inline="same"))
        assert [a.inline for a in collector.styles()] == ["same"]
        assert [a.inline for a in collector.scripts()] == ["same"]

    def test_inline_asset_ignores_prepend_flag(
        self, collector: StaticCollector
    ) -> None:
        """``prepend=True`` is ignored for inline assets, they always append."""
        collector.add(StaticAsset(url=JS_URL, kind="js"), prepend=True)
        collector.add(StaticAsset(url="", kind="js", inline="inline()"), prepend=True)
        scripts = collector.scripts()
        assert scripts[0].url == JS_URL
        assert scripts[-1].inline == "inline()"

    def test_inline_and_url_assets_coexist(self, collector: StaticCollector) -> None:
        """URL-form deps prepend, co-located files append, inline blocks append last."""
        collector.add(StaticAsset(url="/dep.js", kind="js"), prepend=True)
        collector.add(StaticAsset(url="/file.js", kind="js"))
        collector.add(StaticAsset(url="", kind="js", inline="one()"))
        collector.add(StaticAsset(url="", kind="js", inline="two()"))
        scripts = collector.scripts()
        assert [(a.url, a.inline) for a in scripts] == [
            ("/dep.js", None),
            ("/file.js", None),
            ("", "one()"),
            ("", "two()"),
        ]

    def test_inline_asset_with_unknown_kind_is_ignored(
        self, collector: StaticCollector
    ) -> None:
        """Unknown kinds fall through the warning path even for inline assets."""
        collector.add(StaticAsset(url="", kind="unknown", inline="data"))
        assert collector.styles() == []
        assert collector.scripts() == []


class TestDiscoveryDedup:
    """End-to-end dedup across discovery sources that share URLs."""

    def test_same_module_url_on_two_components_kept_once(
        self, tmp_path: Path, collector: StaticCollector
    ) -> None:
        """Two components listing the same CDN URL produce one collector entry."""
        shared_cdn = "https://cdn.example.com/lib.js"
        for name in ("alpha", "beta"):
            comp_dir = tmp_path / "_components" / name
            comp_dir.mkdir(parents=True)
            (comp_dir / "component.djx").write_text(f"<div>{name}</div>")
            module_path = comp_dir / "component.py"
            module_path.write_text(f"scripts = [{shared_cdn!r}]\n")
            info = ComponentInfo(
                name=name,
                scope_root=tmp_path,
                scope_relative="",
                template_path=comp_dir / "component.djx",
                module_path=module_path,
                is_simple=False,
            )
            AssetDiscovery(StaticManager()).discover_component_assets(info, collector)
        assert [a.url for a in collector.scripts()] == [shared_cdn]

    def test_same_component_rendered_twice_registers_once(
        self,
        composite_component: ComponentInfo,
        collector: StaticCollector,
        fresh_manager: StaticManager,
    ) -> None:
        """Invoking ``discover_component_assets`` twice collapses to a single entry."""
        discovery = AssetDiscovery(fresh_manager)
        discovery.discover_component_assets(composite_component, collector)
        discovery.discover_component_assets(composite_component, collector)
        style_urls = [a.url for a in collector.styles()]
        script_urls = [a.url for a in collector.scripts()]
        assert style_urls.count("https://cdn.example.com/extra.css") == 1
        assert script_urls.count("https://cdn.example.com/extra.js") == 1
        assert sum(u.endswith("/widget.css") for u in style_urls) == 1
        assert sum(u.endswith("/widget.js") for u in script_urls) == 1


class TestKindToExtension:
    """``_kind_to_extension`` maps collector kinds to file extensions."""

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("css", ".css"), ("js", ".js")],
    )
    def test_known_kinds(self, kind: str, expected: str) -> None:
        """Known kinds map to the matching file extension."""
        assert _kind_to_extension(kind) == expected

    def test_unknown_kind_raises(self) -> None:
        """Passing an unsupported kind raises ``ValueError``."""
        with pytest.raises(ValueError, match="Unsupported asset kind"):
            _kind_to_extension("other")


class TestLoadPythonModule:
    """``_load_python_module`` loads arbitrary ``.py`` files by path."""

    def test_loads_module_exposing_attributes(self, tmp_path: Path) -> None:
        """A valid ``.py`` file is executed and its attributes are accessible."""
        source = tmp_path / "mod.py"
        source.write_text("X = 1\nY = 'ok'\n")
        module = _load_python_module(source)
        assert module is not None
        assert module.X == 1
        assert module.Y == "ok"

    def test_syntax_error_returns_none(self, tmp_path: Path) -> None:
        """A broken source file logs a debug message and returns ``None``."""
        source = tmp_path / "bad.py"
        source.write_text("def (")
        assert _load_python_module(source) is None

    def test_missing_spec_returns_none(self, tmp_path: Path) -> None:
        """When ``spec_from_file_location`` returns ``None`` the loader returns ``None``."""
        source = tmp_path / "mod.py"
        source.write_text("X = 1")
        with patch(
            "next.static.importlib.util.spec_from_file_location", return_value=None
        ):
            assert _load_python_module(source) is None


class TestReadStringList:
    """``_read_string_list`` reads sanitized string lists from module attributes."""

    def test_list_of_strings(self) -> None:
        """A list of strings is returned intact as a list of strings."""
        mod = type("M", (), {"urls": ["a", "b"]})()
        assert _read_string_list(mod, "urls") == ["a", "b"]

    def test_tuple_of_strings(self) -> None:
        """Tuples are accepted and normalized to a list."""
        mod = type("M", (), {"urls": ("a", "b")})()
        assert _read_string_list(mod, "urls") == ["a", "b"]

    def test_non_sequence_attribute_returns_empty(self) -> None:
        """A non list/tuple attribute returns an empty list."""
        mod = type("M", (), {"urls": "not-a-list"})()
        assert _read_string_list(mod, "urls") == []

    def test_missing_attribute_returns_empty(self) -> None:
        """A missing attribute returns an empty list."""
        mod = type("M", (), {})()
        assert _read_string_list(mod, "urls") == []

    def test_drops_empty_and_non_string_items(self) -> None:
        """Empty strings and non-string members are filtered out."""
        mod = type("M", (), {"urls": ["a", "", None, 42, "b"]})()
        assert _read_string_list(mod, "urls") == ["a", "b"]


class TestFileStaticBackend:
    """``FileStaticBackend`` registers files and renders link/script tags."""

    def test_register_returns_prefixed_url(
        self, file_backend: FileStaticBackend, tmp_path: Path
    ) -> None:
        """``register_file`` returns a URL under the ``_next/static`` prefix."""
        source = tmp_path / "thing.css"
        source.write_text("")
        url = file_backend.register_file(source, "about", "css")
        assert url == "/_next/static/about.css"

    def test_lookup_returns_registry_entry(
        self, file_backend: FileStaticBackend, tmp_path: Path
    ) -> None:
        """``lookup`` resolves the stored absolute path for a registered URL."""
        source = tmp_path / "thing.js"
        source.write_text("")
        file_backend.register_file(source, "widget", "js")
        entry = file_backend.lookup("widget.js")
        assert isinstance(entry, _FileRegistryEntry)
        assert entry.source_path == source.resolve()

    def test_lookup_missing_returns_none(self, file_backend: FileStaticBackend) -> None:
        """``lookup`` returns ``None`` for an unknown logical name."""
        assert file_backend.lookup("missing.css") is None

    def test_render_default_tags(self, file_backend: FileStaticBackend) -> None:
        """The default link/script formatters emit standard HTML markup."""
        assert file_backend.render_link_tag("/a.css") == (
            '<link rel="stylesheet" href="/a.css">'
        )
        assert file_backend.render_script_tag("/a.js") == (
            '<script src="/a.js"></script>'
        )

    def test_custom_tag_templates_from_options(self) -> None:
        """``OPTIONS`` customize link/script tag templates via ``{url}`` placeholder."""
        backend = FileStaticBackend(
            {
                "OPTIONS": {
                    "css_tag": '<link data-custom href="{url}">',
                    "js_tag": '<script data-x src="{url}"></script>',
                }
            }
        )
        assert backend.render_link_tag("/a.css") == ('<link data-custom href="/a.css">')
        assert backend.render_script_tag("/a.js") == (
            '<script data-x src="/a.js"></script>'
        )

    def test_generate_urls_returns_single_catch_all(
        self, file_backend: FileStaticBackend
    ) -> None:
        """``generate_urls`` returns a single catch-all URL pattern."""
        patterns = file_backend.generate_urls()
        assert len(patterns) == 1
        assert patterns[0].name == "next_static_serve"

    def test_clear_registry_wipes_every_mapping(
        self, file_backend: FileStaticBackend, tmp_path: Path
    ) -> None:
        """``clear_registry`` resets the internal mapping to empty."""
        (tmp_path / "a.css").write_text("")
        file_backend.register_file(tmp_path / "a.css", "a", "css")
        file_backend.clear_registry()
        assert file_backend.lookup("a.css") is None


class TestStaticBackendABC:
    """``StaticBackend`` is abstract and cannot be instantiated directly."""

    def test_cannot_instantiate_abstract(self) -> None:
        """Attempting to construct the ABC raises ``TypeError``."""
        with pytest.raises(TypeError):
            StaticBackend()  # type: ignore[abstract]


class TestStaticsFactory:
    """``StaticsFactory`` constructs backend instances from config dicts."""

    def test_default_backend_is_file_static_backend(self) -> None:
        """An empty config resolves to the default ``FileStaticBackend``."""
        backend = StaticsFactory.create_backend({})
        assert isinstance(backend, FileStaticBackend)

    def test_explicit_backend_path(self) -> None:
        """An explicit ``BACKEND`` path instantiates the named class."""
        backend = StaticsFactory.create_backend(
            {"BACKEND": "next.static.FileStaticBackend", "OPTIONS": {}}
        )
        assert isinstance(backend, FileStaticBackend)

    def test_non_backend_class_raises(self) -> None:
        """A class that is not a ``StaticBackend`` subclass raises ``TypeError``."""
        with pytest.raises(TypeError, match="is not a StaticBackend subclass"):
            StaticsFactory.create_backend({"BACKEND": "builtins.dict"})


class TestAssetDiscoveryPageAssets:
    """``AssetDiscovery.discover_page_assets`` walks layouts, templates, modules."""

    def _make_discovery(
        self,
        file_backend: FileStaticBackend,
        page_roots: tuple[Path, ...] = (),
    ) -> AssetDiscovery:
        """Build a discovery helper wired to a ``StaticManager`` stub."""
        manager = StaticManager()
        manager._backends = [file_backend]
        manager._cached_page_roots = page_roots
        return AssetDiscovery(manager)

    def test_walks_nested_layouts_then_template_then_module(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """Outer-to-inner layout assets precede template and module-level lists."""
        page_root = tmp_path / "pages"
        inner = page_root / "blog"
        inner.mkdir(parents=True)
        (page_root / "layout.djx").write_text("root")
        (page_root / "layout.css").write_text("/* root */")
        (page_root / "layout.js").write_text("/* root js */")
        (inner / "layout.djx").write_text("inner")
        (inner / "layout.css").write_text("/* inner */")
        (inner / "template.css").write_text("/* tpl */")
        (inner / "template.js").write_text("/* tpl js */")
        page_py = inner / "page.py"
        page_py.write_text(
            'styles = ["https://ext/inter.css"]\nscripts = ["https://ext/app.js"]\n'
        )

        discovery = self._make_discovery(file_backend, (page_root.resolve(),))
        discovery.discover_page_assets(page_py, collector)

        style_urls = [asset.url for asset in collector.styles()]
        script_urls = [asset.url for asset in collector.scripts()]
        assert style_urls == [
            "/_next/static/layout.css",
            "/_next/static/blog/layout.css",
            "/_next/static/blog.css",
            "https://ext/inter.css",
        ]
        assert script_urls == [
            "/_next/static/layout.js",
            "/_next/static/blog.js",
            "https://ext/app.js",
        ]

    def test_no_layout_directories_still_collects_template_and_module(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """When no layout exists discovery still collects template and module lists."""
        page_root = tmp_path / "pages"
        page_root.mkdir()
        (page_root / "template.css").write_text("/* tpl */")
        page_py = page_root / "page.py"
        page_py.write_text('styles = ["https://ext/x.css"]\n')

        discovery = self._make_discovery(file_backend, (page_root.resolve(),))
        discovery.discover_page_assets(page_py, collector)

        assert [asset.url for asset in collector.styles()] == [
            "/_next/static/index.css",
            "https://ext/x.css",
        ]

    def test_missing_page_py_skips_module_list_collection(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """A non-existent ``page.py`` is silently skipped."""
        page_root = tmp_path / "pages"
        page_root.mkdir()
        discovery = self._make_discovery(file_backend, (page_root.resolve(),))
        discovery.discover_page_assets(page_root / "page.py", collector)
        assert collector.styles() == []
        assert collector.scripts() == []

    def test_layout_and_template_outside_page_root_use_fallback_names(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """Files outside any configured page root fall back to directory-based names."""
        strangedir = tmp_path / "strange"
        strangedir.mkdir()
        (strangedir / "layout.djx").write_text("")
        (strangedir / "layout.css").write_text("")
        (strangedir / "template.css").write_text("")
        page_py = strangedir / "page.py"
        page_py.write_text("")

        discovery = self._make_discovery(file_backend, ())
        discovery.discover_page_assets(page_py, collector)

        style_urls = [asset.url for asset in collector.styles()]
        assert "/_next/static/strange/layout.css" in style_urls
        assert "/_next/static/strange.css" in style_urls


class TestAssetDiscoveryComponentAssets:
    """``AssetDiscovery.discover_component_assets`` handles composite components."""

    def _discovery(
        self, backend: FileStaticBackend
    ) -> tuple[AssetDiscovery, StaticManager]:
        """Build a ``StaticManager``/``AssetDiscovery`` pair wired to ``backend``."""
        manager = StaticManager()
        manager._backends = [backend]
        return AssetDiscovery(manager), manager

    def test_simple_component_is_ignored(
        self,
        simple_component: ComponentInfo,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """Simple components have no co-located assets and are skipped."""
        discovery, _ = self._discovery(file_backend)
        discovery.discover_component_assets(simple_component, collector)
        assert collector.styles() == []
        assert collector.scripts() == []

    def test_composite_collects_css_js_and_module_lists(
        self,
        composite_component: ComponentInfo,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """Composite components contribute co-located files and module lists."""
        discovery, _ = self._discovery(file_backend)
        discovery.discover_component_assets(composite_component, collector)

        style_urls = [asset.url for asset in collector.styles()]
        script_urls = [asset.url for asset in collector.scripts()]
        assert style_urls == [
            "/_next/static/components/widget.css",
            "https://cdn.example.com/extra.css",
        ]
        assert script_urls == [
            "/_next/static/components/widget.js",
            "https://cdn.example.com/extra.js",
        ]

    def test_composite_without_files_uses_only_module_lists(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """A composite that has no css/js files reads only the module lists."""
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        template_path = comp_dir / "component.djx"
        template_path.write_text("<div/>")
        module_path = comp_dir / "component.py"
        module_path.write_text('styles = ["https://a"]\nscripts = ["https://b"]\n')
        info = ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=template_path,
            module_path=module_path,
            is_simple=False,
        )
        discovery, _ = self._discovery(file_backend)
        discovery.discover_component_assets(info, collector)
        assert [a.url for a in collector.styles()] == ["https://a"]
        assert [a.url for a in collector.scripts()] == ["https://b"]

    def test_component_directory_falls_back_to_module_path_parent(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """When ``template_path`` is ``None`` the directory is derived from the module path."""
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        module_path = comp_dir / "component.py"
        module_path.write_text('styles = ["https://from-module"]\n')
        (comp_dir / "component.css").write_text(".w {}")
        info = ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=None,
            module_path=module_path,
            is_simple=False,
        )
        discovery, _ = self._discovery(file_backend)
        discovery.discover_component_assets(info, collector)
        style_urls = [a.url for a in collector.styles()]
        assert "/_next/static/components/widget.css" in style_urls
        assert "https://from-module" in style_urls

    def test_component_with_no_paths_is_skipped(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """A composite with neither template nor module path contributes nothing."""
        info = ComponentInfo(
            name="ghost",
            scope_root=tmp_path,
            scope_relative="",
            template_path=None,
            module_path=None,
            is_simple=False,
        )
        discovery, _ = self._discovery(file_backend)
        discovery.discover_component_assets(info, collector)
        assert collector.styles() == []
        assert collector.scripts() == []

    def test_missing_module_path_skips_module_list_collection(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """A composite whose module path does not exist contributes only files."""
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        template_path = comp_dir / "component.djx"
        template_path.write_text("<div/>")
        (comp_dir / "component.css").write_text(".w {}")
        info = ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=template_path,
            module_path=comp_dir / "does-not-exist.py",
            is_simple=False,
        )
        discovery, _ = self._discovery(file_backend)
        discovery.discover_component_assets(info, collector)
        style_urls = [a.url for a in collector.styles()]
        assert style_urls == ["/_next/static/components/widget.css"]


class TestCollectModuleListsInternals:
    """``_collect_module_lists`` short-circuits when the module cannot load."""

    def test_unloadable_module_is_silently_ignored(
        self,
        tmp_path: Path,
        file_backend: FileStaticBackend,
        collector: StaticCollector,
    ) -> None:
        """A ``_load_python_module`` returning ``None`` leaves the collector empty."""
        manager = StaticManager()
        manager._backends = [file_backend]
        discovery = AssetDiscovery(manager)
        module_path = tmp_path / "page.py"
        module_path.write_text("X = 1")
        with patch("next.static._load_python_module", return_value=None):
            discovery._collect_module_lists(module_path, collector)
        assert collector.styles() == []
        assert collector.scripts() == []


class TestAssetDiscoveryInternals:
    """Internal helpers of ``AssetDiscovery`` cover fallback and error paths."""

    def _discovery(self, backend: FileStaticBackend) -> AssetDiscovery:
        """Return a discovery helper wired to a blank manager and ``backend``."""
        manager = StaticManager()
        manager._backends = [backend]
        return AssetDiscovery(manager)

    def test_register_file_failure_logs_and_skips_collector(
        self,
        tmp_path: Path,
        collector: StaticCollector,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Backend errors during ``register_file`` are logged and collector is untouched."""

        class _RaisingBackend(FileStaticBackend):
            def register_file(self, source_path, logical_name, kind):
                msg = "boom"
                raise OSError(msg)

        discovery = self._discovery(_RaisingBackend())
        source = tmp_path / "x.css"
        source.write_text("")
        with caplog.at_level("DEBUG", logger="next.static"):
            discovery._register_file(source, "a", "css", collector)
        assert collector.styles() == []
        assert any("Failed to register" in rec.message for rec in caplog.records)

    def test_fallback_logical_name_for_empty_directory_name(
        self, file_backend: FileStaticBackend
    ) -> None:
        """Directories with an empty ``name`` fall back to ``index``."""
        discovery = self._discovery(file_backend)
        assert discovery._fallback_logical_name(Path("/")) == "index"

    def test_find_page_root_returns_none_when_outside(
        self, tmp_path: Path, file_backend: FileStaticBackend
    ) -> None:
        """``_find_page_root`` returns ``None`` for a path outside any root."""
        discovery = self._discovery(file_backend)
        assert discovery._find_page_root(tmp_path / "page.py") is None

    def test_logical_name_for_template_outside_root_uses_fallback(
        self, tmp_path: Path, file_backend: FileStaticBackend
    ) -> None:
        """A template directory outside ``page_root`` falls back to its name."""
        discovery = self._discovery(file_backend)
        other = tmp_path / "somewhere"
        other.mkdir()
        (tmp_path / "root").mkdir()
        name = discovery._logical_name_for_template(other, tmp_path / "root")
        assert name == "somewhere"

    def test_logical_name_for_layout_outside_root_uses_fallback(
        self, tmp_path: Path, file_backend: FileStaticBackend
    ) -> None:
        """A layout directory outside ``page_root`` falls back to ``<name>/layout``."""
        discovery = self._discovery(file_backend)
        other = tmp_path / "somewhere"
        other.mkdir()
        (tmp_path / "root").mkdir()
        name = discovery._logical_name_for_layout(other, tmp_path / "root")
        assert name == "somewhere/layout"

    def test_logical_name_for_layout_without_root(
        self, tmp_path: Path, file_backend: FileStaticBackend
    ) -> None:
        """Without a ``page_root`` the layout name falls back to ``<name>/layout``."""
        discovery = self._discovery(file_backend)
        layout_dir = tmp_path / "pages"
        layout_dir.mkdir()
        assert discovery._logical_name_for_layout(layout_dir, None) == "pages/layout"

    def test_logical_name_for_layout_at_page_root_returns_plain_layout(
        self, tmp_path: Path, file_backend: FileStaticBackend
    ) -> None:
        """When ``layout_dir == page_root`` the logical name is just ``layout``."""
        discovery = self._discovery(file_backend)
        page_root = tmp_path / "pages"
        page_root.mkdir()
        assert discovery._logical_name_for_layout(page_root, page_root) == "layout"

    def test_find_layout_directories_stops_at_filesystem_root(
        self, tmp_path: Path, file_backend: FileStaticBackend
    ) -> None:
        """With no ``page_root`` the walk terminates at the filesystem root."""
        discovery = self._discovery(file_backend)
        leaf = tmp_path / "a" / "b"
        leaf.mkdir(parents=True)
        page_py = leaf / "page.py"
        page_py.write_text("")
        assert discovery._find_layout_directories(page_py, None) == []


class TestStaticManagerLifecycle:
    """``StaticManager`` manages backend loading, iteration, and reload."""

    def test_iter_yields_url_patterns(self, fresh_manager: StaticManager) -> None:
        """Iterating the manager yields patterns contributed by every backend."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
                    {"BACKEND": "next.static.FileStaticBackend"}
                ]
            }
        ):
            patterns = list(fresh_manager)
        assert len(patterns) == 1

    def test_len_reflects_backend_count(self, fresh_manager: StaticManager) -> None:
        """``len`` returns the number of loaded backends."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
                    {"BACKEND": "next.static.FileStaticBackend"}
                ]
            }
        ):
            assert len(fresh_manager) == 1

    def test_default_backend_triggers_lazy_load(
        self, fresh_manager: StaticManager
    ) -> None:
        """Accessing ``default_backend`` loads configured backends on first access."""
        assert isinstance(fresh_manager.default_backend, FileStaticBackend)

    def test_discovery_is_lazy_and_cached(self, fresh_manager: StaticManager) -> None:
        """``discovery`` is created once and reused on subsequent access."""
        first = fresh_manager.discovery
        second = fresh_manager.discovery
        assert first is second

    def test_falls_back_to_file_backend_when_config_empty(
        self, fresh_manager: StaticManager
    ) -> None:
        """An empty ``DEFAULT_STATIC_BACKENDS`` list keeps a default backend."""
        with override_settings(NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": []}):
            assert isinstance(fresh_manager.default_backend, FileStaticBackend)

    def test_ignores_non_list_configs(self, fresh_manager: StaticManager) -> None:
        """A non-list ``DEFAULT_STATIC_BACKENDS`` value is coerced to an empty list."""
        from next.static import next_framework_settings as conf  # noqa: PLC0415

        conf._attr_value_cache["DEFAULT_STATIC_BACKENDS"] = "not-a-list"
        assert isinstance(fresh_manager.default_backend, FileStaticBackend)

    def test_ignores_non_dict_backend_entries(
        self, fresh_manager: StaticManager
    ) -> None:
        """Non-dict entries inside the backends list are silently skipped."""
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": ["nope", None, 42]}
        ):
            assert isinstance(fresh_manager.default_backend, FileStaticBackend)

    def test_backend_creation_failure_is_logged_and_skipped(
        self,
        fresh_manager: StaticManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When a backend import fails the error is logged and the slot is skipped."""
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "DEFAULT_STATIC_BACKENDS": [
                        {"BACKEND": "builtins.dict"},
                    ]
                }
            ),
            caplog.at_level("ERROR", logger="next.static"),
        ):
            assert isinstance(fresh_manager.default_backend, FileStaticBackend)
        assert any(
            "Error creating static backend" in rec.message for rec in caplog.records
        )

    def test_discover_page_assets_delegates_to_discovery(
        self,
        fresh_manager: StaticManager,
        tmp_path: Path,
        collector: StaticCollector,
    ) -> None:
        """``discover_page_assets`` forwards to the shared ``AssetDiscovery`` instance."""
        page_py = tmp_path / "page.py"
        page_py.write_text('styles = ["https://ext/a.css"]\n')
        fresh_manager.discover_page_assets(page_py, collector)
        assert [a.url for a in collector.styles()] == ["https://ext/a.css"]

    def test_discover_component_assets_delegates_to_discovery(
        self,
        fresh_manager: StaticManager,
        composite_component: ComponentInfo,
        collector: StaticCollector,
    ) -> None:
        """``discover_component_assets`` forwards to the shared ``AssetDiscovery``."""
        fresh_manager.discover_component_assets(composite_component, collector)
        assert any(a.url.endswith("components/widget.css") for a in collector.styles())


class TestStaticManagerInject:
    """``StaticManager.inject`` rewrites placeholders into rendered HTML."""

    def test_injects_css_and_js_tags(
        self, fresh_manager: StaticManager, collector: StaticCollector
    ) -> None:
        """Both CSS and JS placeholders are replaced with concatenated tags."""
        collector.add(StaticAsset(url="/a.css", kind="css"))
        collector.add(StaticAsset(url="/b.css", kind="css"))
        collector.add(StaticAsset(url="/a.js", kind="js"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"
        out = fresh_manager.inject(html, collector)
        assert '<link rel="stylesheet" href="/a.css">' in out
        assert '<link rel="stylesheet" href="/b.css">' in out
        assert '<script src="/a.js"></script>' in out
        assert STYLES_PLACEHOLDER not in out
        assert SCRIPTS_PLACEHOLDER not in out

    def test_noop_without_placeholders(
        self, fresh_manager: StaticManager, collector: StaticCollector
    ) -> None:
        """HTML without any placeholder is returned unchanged."""
        collector.add(StaticAsset(url="/a.css", kind="css"))
        assert fresh_manager.inject("<p>plain</p>", collector) == "<p>plain</p>"

    def test_empty_collector_renders_empty_slots(
        self, fresh_manager: StaticManager, collector: StaticCollector
    ) -> None:
        """An empty collector replaces placeholders with empty strings."""
        html = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"
        out = fresh_manager.inject(html, collector)
        assert out == "<head></head><body></body>"

    def test_inline_script_body_emitted_verbatim(
        self, fresh_manager: StaticManager, collector: StaticCollector
    ) -> None:
        """Inline script bodies are written into the slot without any wrapping."""
        body = '<script type="module">const x = 1;</script>'
        collector.add(StaticAsset(url="", kind="js", inline=body))
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        out = fresh_manager.inject(html, collector)
        assert out == f"<body>{body}</body>"

    def test_inline_style_body_emitted_verbatim(
        self, fresh_manager: StaticManager, collector: StaticCollector
    ) -> None:
        """Inline style bodies are written into the slot without any wrapping."""
        body = "<style>.x{color:red}</style>"
        collector.add(StaticAsset(url="", kind="css", inline=body))
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, collector)
        assert out == f"<head>{body}</head>"

    def test_url_and_inline_interleave_in_order(
        self, fresh_manager: StaticManager, collector: StaticCollector
    ) -> None:
        """Inline bodies append after URL deps and appear in registration order."""
        collector.add(StaticAsset(url="/dep.js", kind="js"), prepend=True)
        collector.add(StaticAsset(url="/file.js", kind="js"))
        collector.add(StaticAsset(url="", kind="js", inline="<script>a()</script>"))
        collector.add(StaticAsset(url="", kind="js", inline="<script>b()</script>"))
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        out = fresh_manager.inject(html, collector)
        slot_body = out.removeprefix("<body>").removesuffix("</body>")
        assert slot_body == (
            '<script src="/dep.js"></script>\n'
            '<script src="/file.js"></script>\n'
            "<script>a()</script>\n"
            "<script>b()</script>"
        )


class TestStaticManagerPageRoots:
    """``_page_roots`` caches absolute page directories from page backends."""

    def test_caches_value_after_first_call(
        self, fresh_manager: StaticManager, tmp_path: Path
    ) -> None:
        """Subsequent calls return the cached tuple rather than re-querying."""
        with patch(
            "next.pages.get_pages_directories_for_watch",
            return_value=[tmp_path],
        ) as mock_roots:
            first = fresh_manager._page_roots()
            second = fresh_manager._page_roots()
        assert first == (tmp_path.resolve(),)
        assert second is first
        assert mock_roots.call_count == 1

    def test_returns_empty_when_pages_module_import_fails(
        self, fresh_manager: StaticManager
    ) -> None:
        """When ``next.pages`` cannot be imported the cache falls back to ``()``."""
        import next.pages as next_pages  # noqa: PLC0415

        original = next_pages.get_pages_directories_for_watch
        try:
            del next_pages.get_pages_directories_for_watch
            assert fresh_manager._page_roots() == ()
        finally:
            next_pages.get_pages_directories_for_watch = original

    def test_oserror_on_resolve_is_swallowed(
        self, fresh_manager: StaticManager
    ) -> None:
        """A ``Path.resolve`` OSError is swallowed and the root is dropped."""
        bad = Path("/does/not/matter")
        real_resolve = Path.resolve

        def _resolve(self, *, strict=False):
            if self == bad:
                msg = "boom"
                raise OSError(msg)
            return real_resolve(self, strict=strict)

        with (
            patch("next.pages.get_pages_directories_for_watch", return_value=[bad]),
            patch.object(Path, "resolve", _resolve),
        ):
            assert fresh_manager._page_roots() == ()


class TestStaticServeView:
    """``static_serve_view`` delegates to ``django.views.static.serve``."""

    @pytest.fixture()
    def warm_global_backend(self, tmp_path: Path) -> Generator[Path, None, None]:
        """Prime the module-level ``static_manager`` with a single registered file."""
        static_manager._backends.clear()
        static_manager._discovery = None
        static_manager._cached_page_roots = None
        static_manager._reload_config()
        source = tmp_path / "served.css"
        source.write_text("body{}")
        static_manager.default_backend.register_file(source, "served", "css")
        try:
            yield source
        finally:
            static_manager._backends.clear()
            static_manager._discovery = None
            static_manager._cached_page_roots = None

    def test_serves_registered_file(self, warm_global_backend: Path) -> None:
        """A registered file is served with a 200 response and expected content."""
        request = RequestFactory().get("/_next/static/served.css")
        response = static_serve_view(request, "served.css")
        assert response.status_code == 200
        body = b"".join(response.streaming_content)
        assert body == b"body{}"

    def test_unregistered_path_returns_404(self, warm_global_backend: Path) -> None:
        """An unknown logical path returns a 404 response."""
        request = RequestFactory().get("/_next/static/missing.css")
        response = static_serve_view(request, "missing.css")
        assert response.status_code == 404

    @pytest.mark.usefixtures("_reset_global_static_manager")
    def test_non_file_backend_returns_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the default backend is not a ``FileStaticBackend`` the view 404s."""

        class _Dummy:
            pass

        monkeypatch.setattr(
            StaticManager,
            "default_backend",
            property(lambda self: _Dummy()),
        )
        request = RequestFactory().get("/_next/static/whatever.css")
        response = static_serve_view(request, "whatever.css")
        assert response.status_code == 404


class TestTemplateTags:
    """Template tag entry points are exercised through a real Django engine."""

    @pytest.fixture()
    def engine_template(self) -> type[Template]:
        """Return the Django ``Template`` class with the static tag library loaded."""
        return Template

    def test_collect_styles_outputs_placeholder(self) -> None:
        """``{% collect_styles %}`` emits the raw styles placeholder."""
        tpl = Template("{% load next_static %}{% collect_styles %}")
        assert tpl.render(Context()) == STYLES_PLACEHOLDER

    def test_collect_scripts_outputs_placeholder(self) -> None:
        """``{% collect_scripts %}`` emits the raw scripts placeholder."""
        tpl = Template("{% load next_static %}{% collect_scripts %}")
        assert tpl.render(Context()) == SCRIPTS_PLACEHOLDER

    def test_use_style_registers_url_and_renders_nothing(
        self, collector: StaticCollector
    ) -> None:
        """``{% use_style url %}`` registers an asset and emits no markup."""
        tpl = Template('{% load next_static %}{% use_style "/main.css" %}!')
        output = tpl.render(Context({"_static_collector": collector}))
        assert output == "!"
        assert [a.url for a in collector.styles()] == ["/main.css"]

    def test_use_script_registers_url_and_renders_nothing(
        self, collector: StaticCollector
    ) -> None:
        """``{% use_script url %}`` registers an asset and emits no markup."""
        tpl = Template('{% load next_static %}{% use_script "/main.js" %}!')
        output = tpl.render(Context({"_static_collector": collector}))
        assert output == "!"
        assert [a.url for a in collector.scripts()] == ["/main.js"]

    def test_use_style_prepends_before_appended_files(
        self, collector: StaticCollector
    ) -> None:
        """``use_style`` lands before items appended by co-located discovery."""
        collector.add(StaticAsset(url="/_next/static/layout.css", kind="css"))
        tpl = Template('{% load next_static %}{% use_style "/cdn/dep.css" %}')
        tpl.render(Context({"_static_collector": collector}))
        assert [a.url for a in collector.styles()] == [
            "/cdn/dep.css",
            "/_next/static/layout.css",
        ]

    def test_use_script_prepends_before_appended_files(
        self, collector: StaticCollector
    ) -> None:
        """``use_script`` lands before items appended by co-located discovery."""
        collector.add(StaticAsset(url="/_next/static/layout.js", kind="js"))
        tpl = Template('{% load next_static %}{% use_script "/cdn/dep.js" %}')
        tpl.render(Context({"_static_collector": collector}))
        assert [a.url for a in collector.scripts()] == [
            "/cdn/dep.js",
            "/_next/static/layout.js",
        ]

    @pytest.mark.parametrize(
        ("context_extra", "tpl_src"),
        [
            ({}, '{% load next_static %}{% use_style "/x.css" %}'),
            (
                {"_static_collector": "not-a-collector"},
                '{% load next_static %}{% use_style "/x.css" %}',
            ),
        ],
        ids=["missing_collector", "wrong_type_collector"],
    )
    def test_use_tags_without_collector_noop(
        self, context_extra: dict[str, object], tpl_src: str
    ) -> None:
        """Missing or wrong-typed collector makes the use-tags silent no-ops."""
        tpl = Template(tpl_src)
        assert tpl.render(Context(context_extra)) == ""

    @pytest.mark.parametrize(
        "bad_url",
        ["", None, 42],
        ids=["empty_string", "none", "non_string"],
    )
    def test_use_tags_ignore_invalid_urls(
        self,
        collector: StaticCollector,
        bad_url: object,
    ) -> None:
        """Non-string or empty URLs passed to ``use_style`` are ignored."""
        tpl = Template("{% load next_static %}{% use_style url %}")
        tpl.render(Context({"_static_collector": collector, "url": bad_url}))
        assert collector.styles() == []

    def test_block_use_script_captures_body_and_emits_nothing(
        self, collector: StaticCollector
    ) -> None:
        """``{% #use_script %}`` records the body and emits no markup in place."""
        tpl = Template(
            "{% load next_static %}before"
            "{% #use_script %}<script>inline()</script>{% /use_script %}"
            "after"
        )
        output = tpl.render(Context({"_static_collector": collector}))
        assert output == "beforeafter"
        scripts = collector.scripts()
        assert len(scripts) == 1
        assert scripts[0].inline == "<script>inline()</script>"
        assert scripts[0].url == ""

    def test_block_use_style_captures_body_and_emits_nothing(
        self, collector: StaticCollector
    ) -> None:
        """``{% #use_style %}`` records the body and emits no markup in place."""
        tpl = Template(
            "{% load next_static %}"
            "{% #use_style %}<style>.a{color:red}</style>{% /use_style %}"
        )
        output = tpl.render(Context({"_static_collector": collector}))
        assert output == ""
        styles = collector.styles()
        assert len(styles) == 1
        assert styles[0].inline == "<style>.a{color:red}</style>"

    def test_block_use_script_renders_body_with_context(
        self, collector: StaticCollector
    ) -> None:
        """Block body is rendered against the active context, so vars are substituted."""
        tpl = Template(
            "{% load next_static %}"
            "{% #use_script %}<script>id={{ widget_id }};</script>{% /use_script %}"
        )
        tpl.render(Context({"_static_collector": collector, "widget_id": "likes"}))
        assert collector.scripts()[0].inline == "<script>id=likes;</script>"

    def test_block_use_script_appends_after_url_deps(
        self, collector: StaticCollector
    ) -> None:
        """Block form appends, URL form prepends. URL deps land before inline bodies."""
        tpl = Template(
            "{% load next_static %}"
            '{% use_script "/cdn/react.js" %}'
            "{% #use_script %}<script>boot()</script>{% /use_script %}"
        )
        tpl.render(Context({"_static_collector": collector}))
        assert [(a.url, a.inline) for a in collector.scripts()] == [
            ("/cdn/react.js", None),
            ("", "<script>boot()</script>"),
        ]

    def test_block_use_script_blank_body_is_ignored(
        self, collector: StaticCollector
    ) -> None:
        """A block body that renders to whitespace-only never reaches the collector."""
        tpl = Template(
            "{% load next_static %}{% #use_script %}   \n  {% /use_script %}"
        )
        tpl.render(Context({"_static_collector": collector}))
        assert collector.scripts() == []

    @pytest.mark.parametrize(
        "tpl_src",
        [
            "{% load next_static %}"
            "{% #use_script %}<script>x()</script>{% /use_script %}",
            "{% load next_static %}"
            "{% #use_style %}<style>.a{color:red}</style>{% /use_style %}",
        ],
        ids=["block_use_script", "block_use_style"],
    )
    def test_block_tags_without_collector_noop(self, tpl_src: str) -> None:
        """Block tags are silent no-ops when no collector is in context."""
        assert Template(tpl_src).render(Context()) == ""

    def test_block_use_script_duplicate_bodies_dedupe(
        self, collector: StaticCollector
    ) -> None:
        """Two block occurrences with identical rendered bodies collapse to one entry."""
        tpl = Template(
            "{% load next_static %}"
            "{% #use_script %}<script>same()</script>{% /use_script %}"
            "{% #use_script %}<script>same()</script>{% /use_script %}"
        )
        tpl.render(Context({"_static_collector": collector}))
        assert len(collector.scripts()) == 1

    def test_block_use_script_different_context_stays_distinct(
        self, collector: StaticCollector
    ) -> None:
        """Blocks that interpolate different context into the body stay distinct."""
        tpl = Template(
            "{% load next_static %}"
            "{% #use_script %}<script>mount('{{ id }}')</script>{% /use_script %}"
        )
        for instance_id in ("likes", "stars"):
            tpl.render(Context({"_static_collector": collector, "id": instance_id}))
        bodies = [a.inline for a in collector.scripts()]
        assert bodies == [
            "<script>mount('likes')</script>",
            "<script>mount('stars')</script>",
        ]

    def test_block_use_script_same_context_dedups_across_renders(
        self, collector: StaticCollector
    ) -> None:
        """The same block rendered repeatedly with the same context emits once."""
        tpl = Template(
            "{% load next_static %}"
            "{% #use_script %}<script>boot('{{ id }}')</script>{% /use_script %}"
        )
        ctx = Context({"_static_collector": collector, "id": "shared"})
        tpl.render(ctx)
        tpl.render(ctx)
        assert len(collector.scripts()) == 1
        assert collector.scripts()[0].inline == "<script>boot('shared')</script>"


class TestComponentTagIntegration:
    """The ``{% component %}`` tag forwards to ``static_manager`` for composites."""

    @pytest.mark.usefixtures("_reset_global_static_manager")
    def test_component_render_discovers_assets_via_static_manager(
        self,
        composite_component: ComponentInfo,
        collector: StaticCollector,
    ) -> None:
        """Rendering a composite with a collector in context triggers discovery."""
        from next.components import components_manager  # noqa: PLC0415

        static_manager._reload_config()
        with patch.object(
            components_manager,
            "get_component",
            return_value=composite_component,
        ):
            tpl = Template('{% load components %}{% component "widget" %}')
            tpl.render(
                Context(
                    {
                        "current_template_path": str(composite_component.template_path),
                        "_static_collector": collector,
                    }
                )
            )
        style_urls = [a.url for a in collector.styles()]
        assert any(u.endswith("components/widget.css") for u in style_urls)


class TestStaticManagerGlobal:
    """The module-level ``static_manager`` is a live ``StaticManager`` instance."""

    def test_is_static_manager_instance(self) -> None:
        """The exported singleton is an instance of ``StaticManager``."""
        assert isinstance(static_manager, StaticManager)
