from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings

from next.components import (
    ComponentInfo,
    ComponentRenderer,
    ComponentScanner,
    ComponentsManager,
    ComponentTemplateLoader,
    FileComponentsBackend,
    ModuleLoader,
    components_manager,
    get_component,
    get_component_paths_for_watch,
    load_component_template,
    register_components_folder_from_router_walk,
    render_component,
)
from next.conf import next_framework_settings
from tests.support import (
    next_framework_settings_component_backends_list as _next_framework_settings_component_backends_list,
)


# ---------------------------------------------------------------------------
# TestComponentsManager
# ---------------------------------------------------------------------------


class TestComponentsManager:
    """Tests for ComponentsManager."""

    def test_get_component_empty_when_no_config(self) -> None:
        """When ``BACKENDS`` is empty, get_component returns None."""
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.components.manager.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager._reload_config()
            assert manager.get_component("card", Path("/tmp/t.djx")) is None

    def test_collect_visible_components_merges_backends(self) -> None:
        """collect_visible_components merges from all backends, first wins."""
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.components.manager.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager._reload_config()
            assert manager.collect_visible_components(Path("/x")) == {}

    def test_reload_config_swallows_backend_creation_error(self) -> None:
        """When create_backend raises, _reload_config logs and continues."""
        mock_ns = _next_framework_settings_component_backends_list(
            [
                {"BACKEND": "next.components.NonexistentBackend", "OPTIONS": {}},
            ],
        )
        with patch("next.components.manager.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager._reload_config()
            assert len(manager._backends) == 0

    def test_template_loader_built_with_default_module_loader(self) -> None:
        """Render pipeline uses ``ComponentTemplateLoader`` wrapping ``ModuleLoader``."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.components.manager.next_framework_settings", mock_ns):
            mgr._reload_config()
        assert isinstance(mgr.template_loader, ComponentTemplateLoader)
        assert isinstance(mgr.component_renderer, ComponentRenderer)


# ---------------------------------------------------------------------------
# TestRegisterComponentsFolderFromRouterWalk
# ---------------------------------------------------------------------------


class TestRegisterComponentsFolderFromRouterWalk:
    """``register_components_folder_from_router_walk`` wiring."""

    def test_registers_scanned_components_on_backend(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Each folder is scanned into the first file components backend registry."""
        components_manager._walk_registered_folders.clear()
        components_manager._backends.clear()
        backend = FileComponentsBackend(dict(min_component_config))
        components_manager._backends.append(backend)
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "z.djx").write_text("z")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        names = [c.name for c in backend._registry.get_all()]
        assert "z" in names

    def test_second_call_skips_same_resolved_folder(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Repeated registration for the same path is ignored."""
        components_manager._walk_registered_folders.clear()
        components_manager._backends.clear()
        backend = FileComponentsBackend(dict(min_component_config))
        components_manager._backends.append(backend)
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "a.djx").write_text("a")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        assert len(list(backend._registry.get_all())) == 1

    def test_loads_component_py_when_composite_has_module(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Router walk loads ``component.py`` for composite components (coverage)."""
        components_manager._walk_registered_folders.clear()
        components_manager._backends.clear()
        backend = FileComponentsBackend(dict(min_component_config))
        components_manager._backends.append(backend)
        comp_dir = tmp_path / "_components" / "news"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("<span>news</span>")
        (comp_dir / "component.py").write_text("# module for news\n")
        register_components_folder_from_router_walk(
            tmp_path / "_components",
            tmp_path,
            "",
        )
        infos = [i for i in backend._registry.get_all() if i.name == "news"]
        assert len(infos) == 1
        assert infos[0].module_path is not None

    def test_import_all_component_modules_loads_each_module_path(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """``import_all_component_modules`` executes ``module_loader.load`` per path."""
        comp_py = tmp_path / "component.py"
        comp_py.write_text("# registered component module\n")
        djx = tmp_path / "c.djx"
        djx.write_text("<div/>")
        info = ComponentInfo(
            name="c",
            scope_root=tmp_path,
            scope_relative="",
            template_path=djx,
            module_path=comp_py,
            is_simple=False,
        )
        backend = FileComponentsBackend(dict(min_component_config))
        backend._registry.register(info)
        backend.import_all_component_modules()


# ---------------------------------------------------------------------------
# TestGetComponent
# ---------------------------------------------------------------------------


class TestGetComponent:
    """Tests for get_component()."""

    def test_get_component_delegates_to_manager(self) -> None:
        """get_component uses components_manager."""
        with patch("next.components.facade.components_manager") as mock_mgr:
            mock_mgr.get_component.return_value = None
            assert get_component("x", Path("/t")) is None
            mock_mgr.get_component.assert_called_once_with("x", Path("/t"))


# ---------------------------------------------------------------------------
# TestLoadComponentTemplate
# ---------------------------------------------------------------------------


class TestLoadComponentTemplate:
    """Tests for load_component_template()."""

    def test_load_simple_djx(self, tmp_path: Path) -> None:
        """Load template from .djx file."""
        (tmp_path / "card.djx").write_text("<div>{{ title }}</div>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        content = load_component_template(info)
        assert content == "<div>{{ title }}</div>"

    def test_load_returns_none_for_missing_file(self) -> None:
        """Returns None when template_path does not exist."""
        info = ComponentInfo(
            name="card",
            scope_root=Path("/nonexistent"),
            scope_relative="",
            template_path=Path("/nonexistent/card.djx"),
            module_path=None,
            is_simple=True,
        )
        assert load_component_template(info) is None


# ---------------------------------------------------------------------------
# TestRenderComponent
# ---------------------------------------------------------------------------


class TestRenderComponent:
    """Tests for render_component()."""

    def test_render_simple_component(self, tmp_path: Path) -> None:
        """Simple component renders with context."""
        (tmp_path / "card.djx").write_text("<h3>{{ title }}</h3>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        html = render_component(info, {"title": "Hello"})
        assert "<h3>Hello</h3>" in html

    def test_render_composite_with_module_no_render_uses_template(
        self, tmp_path: Path
    ) -> None:
        """Composite with component.djx and component.py without render uses template."""
        (tmp_path / "profile").mkdir()
        (tmp_path / "profile" / "component.djx").write_text(
            "<div>{{ username }}</div>",
        )
        (tmp_path / "profile" / "component.py").write_text(
            "other = 1\n",
        )
        info = ComponentInfo(
            name="profile",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "profile" / "component.djx",
            module_path=tmp_path / "profile" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {"username": "Admin"})
        assert "Admin" in html

    def test_load_component_template_from_module_string(self, tmp_path: Path) -> None:
        """load_component_template returns module.component when no .djx."""
        (tmp_path / "mod").mkdir()
        (tmp_path / "mod" / "component.py").write_text(
            'component = "<div>{{ x }}</div>"\n',
        )
        info = ComponentInfo(
            name="mod",
            scope_root=tmp_path,
            scope_relative="",
            template_path=None,
            module_path=tmp_path / "mod" / "component.py",
            is_simple=False,
        )
        out = load_component_template(info)
        assert out == "<div>{{ x }}</div>"

    def test_render_composite_module_load_fallback(self, tmp_path: Path) -> None:
        """When composite module fails to load, fall back to template string."""
        (tmp_path / "bad").mkdir()
        (tmp_path / "bad" / "component.djx").write_text("<p>fallback</p>")
        (tmp_path / "bad" / "component.py").write_text("syntax error (")
        info = ComponentInfo(
            name="bad",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "bad" / "component.djx",
            module_path=tmp_path / "bad" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {})
        assert "fallback" in html

    def test_render_composite_with_custom_render(self, tmp_path: Path) -> None:
        """Composite with render() in component.py uses it and returns string."""
        (tmp_path / "custom").mkdir()
        (tmp_path / "custom" / "component.py").write_text(
            "def render(x=''):\n    return f'<div>{x}</div>'\n",
        )
        info = ComponentInfo(
            name="custom",
            scope_root=tmp_path,
            scope_relative="",
            template_path=None,
            module_path=tmp_path / "custom" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {"x": "hello"})
        assert "hello" in html

    def test_render_returns_empty_when_template_unloadable(self) -> None:
        """When template cannot be loaded, returns empty string."""
        info = ComponentInfo(
            name="x",
            scope_root=Path("/none"),
            scope_relative="",
            template_path=Path("/none/x.djx"),
            module_path=None,
            is_simple=True,
        )
        assert render_component(info, {}) == ""


# ---------------------------------------------------------------------------
# TestComponentRenderers
# ---------------------------------------------------------------------------


class TestComponentRenderers:
    """Strategy classes and coordinator."""

    def test_component_renderer_empty_strategies(self) -> None:
        """ComponentRenderer returns empty string when no strategy matches."""
        from next.components import ComponentRenderer

        info = ComponentInfo("x", Path("/"), "", None, None, True)
        assert ComponentRenderer([]).render(info, {}) == ""

    def test_composite_render_module_path_none_guard(self) -> None:
        """CompositeComponentRenderer.render returns '' if module_path is None."""
        from next.components import CompositeComponentRenderer

        loader = ModuleLoader()
        tl = ComponentTemplateLoader(loader)
        r = CompositeComponentRenderer(loader, tl)
        info = ComponentInfo(
            "x",
            Path("/"),
            "",
            Path("/t.djx"),
            None,
            False,
        )
        assert r.render(info, {}, None) == ""

    def test_composite_render_returns_httpresponse_content(
        self, tmp_path: Path
    ) -> None:
        """``render()`` may return ``HttpResponse``. Content is decoded to ``str``."""
        d = tmp_path / "hr"
        d.mkdir()
        (d / "component.py").write_text(
            "from django.http import HttpResponse\n"
            "def render():\n"
            "    return HttpResponse(b'<em>ok</em>')\n",
        )
        info = ComponentInfo(
            "hr",
            tmp_path,
            "",
            None,
            d / "component.py",
            False,
        )
        out = render_component(info, {})
        assert "ok" in out

    def test_composite_template_render_injects_request(self, tmp_path: Path) -> None:
        """_render_with_template adds request to context when provided."""
        from django.test import RequestFactory

        d = tmp_path / "rq"
        d.mkdir()
        (d / "component.djx").write_text("<i>{{ request.path }}</i>")
        (d / "component.py").write_text("# no render\n")
        info = ComponentInfo(
            "rq",
            tmp_path,
            "",
            d / "component.djx",
            d / "component.py",
            False,
        )
        req = RequestFactory().get("/hello")
        html = render_component(info, {}, request=req)
        assert "/hello" in html

    def test_composite_template_render_includes_csrf_token(
        self, tmp_path: Path
    ) -> None:
        """{% csrf_token %} works in component.djx when request is passed."""
        from django.test import RequestFactory

        d = tmp_path / "csrf"
        d.mkdir()
        (d / "component.djx").write_text("{% csrf_token %}")
        (d / "component.py").write_text("# no render\n")
        info = ComponentInfo(
            "csrf",
            tmp_path,
            "",
            d / "component.djx",
            d / "component.py",
            False,
        )
        req = RequestFactory().get("/")
        html = render_component(info, {}, request=req)
        assert "csrfmiddlewaretoken" in html

    def test_merge_csrf_context_no_op_without_request(self) -> None:
        """Early return when ``request`` is None (defensive API)."""
        import next.components as next_components_mod

        ctx: dict[str, object] = {}
        next_components_mod._merge_csrf_context(ctx, None)
        assert "csrf_token" not in ctx

    def test_merge_csrf_context_skips_when_csrf_token_present(self) -> None:
        """Do not replace an existing ``csrf_token`` (caller supplied)."""
        from django.test import RequestFactory

        import next.components as next_components_mod

        req = RequestFactory().get("/")
        existing = "__test_merge_csrf_existing__"
        ctx: dict[str, object] = {"csrf_token": existing}
        next_components_mod._merge_csrf_context(ctx, req)
        assert ctx["csrf_token"] == existing

    def test_render_with_template_returns_empty_when_no_template_string(
        self, tmp_path: Path
    ) -> None:
        """_render_with_template returns '' when template loader yields None."""
        from next.components import CompositeComponentRenderer

        d = tmp_path / "nt"
        d.mkdir()
        (d / "component.djx").write_text("<p>x</p>")
        (d / "component.py").write_text("# template path via djx. no render()\n")
        info = ComponentInfo(
            "nt",
            tmp_path,
            "",
            d / "component.djx",
            d / "component.py",
            False,
        )
        loader = ModuleLoader()
        tl = ComponentTemplateLoader(loader)
        r = CompositeComponentRenderer(loader, tl)
        with patch.object(tl, "load", return_value=None):
            assert r._render_with_template(info, {}, None) == ""

    def test_fallback_template_none_returns_empty(self, tmp_path: Path) -> None:
        """When module load fails and template loader returns None, fallback is empty."""
        from next.components import CompositeComponentRenderer

        d = tmp_path / "nf"
        d.mkdir()
        (d / "component.py").write_text("syntax error (\n")
        info = ComponentInfo(
            "nf",
            tmp_path,
            "",
            None,
            d / "component.py",
            False,
        )
        r = CompositeComponentRenderer(
            ModuleLoader(), ComponentTemplateLoader(ModuleLoader())
        )
        assert r._fallback_to_template(info, {}) == ""

    def test_simple_renderer_passes_request_and_csrf_for_forms(
        self, tmp_path: Path
    ) -> None:
        """SimpleComponentRenderer adds request and csrf_token for {% csrf_token %}."""
        from django.test import RequestFactory

        from next.components import SimpleComponentRenderer

        (tmp_path / "s.djx").write_text("<b>{% csrf_token %}</b>")
        info = ComponentInfo("s", tmp_path, "", tmp_path / "s.djx", None, True)
        tl = ComponentTemplateLoader(ModuleLoader())
        sr = SimpleComponentRenderer(tl)
        req = RequestFactory().get("/")
        html = sr.render(info, {}, request=req)
        assert "csrfmiddlewaretoken" in html
        assert "<b>" in html


# ---------------------------------------------------------------------------
# TestInjectComponentContext
# ---------------------------------------------------------------------------


class TestInjectComponentContext:
    """_inject_component_context early exits."""

    def test_no_op_when_no_module_path(self) -> None:
        """When module_path is None, nothing is merged."""
        from next.components import _inject_component_context

        info = ComponentInfo("s", Path("/"), "", Path("/t.djx"), None, True)
        data: dict[str, object] = {"keep": 1}
        _inject_component_context(info, data, None)
        assert data == {"keep": 1}


# ---------------------------------------------------------------------------
# TestGetComponentPathsForWatch
# ---------------------------------------------------------------------------


class TestGetComponentPathsForWatch:
    """``get_component_paths_for_watch`` mirrors discovery without mutating managers."""

    def test_empty_when_backend_settings_not_lists(self) -> None:
        """Return empty sets when ``DEFAULT_*_BACKENDS`` are not lists."""
        mock_nf = SimpleNamespace(
            DEFAULT_PAGE_BACKENDS="not-a-list",
            DEFAULT_COMPONENT_BACKENDS="not-a-list",
        )
        with patch("next.components.watch.next_framework_settings", mock_nf):
            assert get_component_paths_for_watch() == set()

    def test_collects_composite_under_pages_tree(self, tmp_path: Path) -> None:
        """Paths include ``component.djx`` under ``COMPONENTS_DIR`` in a pages tree."""
        pages_root = tmp_path / "pages"
        comp_dir = pages_root / "_components" / "widget"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("<span/>")
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            paths = get_component_paths_for_watch()
        next_framework_settings.reload()
        assert (comp_dir / "component.djx").resolve() in paths

    def test_includes_module_path_for_py_only_composite(self, tmp_path: Path) -> None:
        """Composite with only ``component.py`` (string template) adds that path."""
        pages_root = tmp_path / "pages"
        comp_dir = pages_root / "_components" / "modonly"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.py").write_text('component = "<b/>"\n')
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            paths = get_component_paths_for_watch()
        next_framework_settings.reload()
        assert (comp_dir / "component.py").resolve() in paths

    def test_skips_non_dict_page_config(self) -> None:
        """Non-dict ``DEFAULT_PAGE_BACKENDS`` entries are ignored."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": ["not-dict"],
                "DEFAULT_COMPONENT_BACKENDS": [],
            },
        ):
            next_framework_settings.reload()
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_non_dict_component_config(self) -> None:
        """Non-dict ``DEFAULT_COMPONENT_BACKENDS`` entries are ignored."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": ["bad"],
            },
        ):
            next_framework_settings.reload()
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_collects_simple_djx_in_extra_root(self, tmp_path: Path) -> None:
        """Extra component root picks up top-level ``.djx`` simple components."""
        root = tmp_path / "extra"
        root.mkdir()
        (root / "solo.djx").write_text("x")
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [str(root)],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            paths = get_component_paths_for_watch()
        next_framework_settings.reload()
        assert (root / "solo.djx").resolve() in paths

    def test_swallows_page_backend_create_error(self, tmp_path: Path) -> None:
        """Invalid page backend config is skipped after logging."""
        pages_root = tmp_path / "pages"
        pages_root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [],
            },
        ):
            next_framework_settings.reload()
            with patch(
                "next.urls.RouterFactory.create_backend",
                side_effect=ValueError("bad config"),
            ):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_glob_oserror_swallowed_for_pages_scan(self, tmp_path: Path) -> None:
        """OSError from ``Path.glob`` while finding component dirs is handled."""
        pages_root = tmp_path / "pages"
        pages_root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            with patch.object(Path, "glob", side_effect=OSError("glob fail")):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_relative_to_valueerror_skips_component_dir(self, tmp_path: Path) -> None:
        """When ``relative_to`` fails, that ``_components`` folder is skipped."""
        pages_root = tmp_path / "pages"
        comp_dir = pages_root / "_components" / "w"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("x")
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            with patch.object(Path, "relative_to", side_effect=ValueError("outside")):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_swallows_component_backend_create_error(self) -> None:
        """Failure to build a component backend is skipped."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            with patch(
                "next.components.manager.ComponentsFactory.create_backend",
                side_effect=RuntimeError("boom"),
            ):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_non_file_component_backend(self) -> None:
        """Non-``FileComponentsBackend`` entries do not contribute paths."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.DummyBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_oserror_scanning_extra_root(self, tmp_path: Path) -> None:
        """OSError when listing an extra component root is handled."""
        root = tmp_path / "r"
        root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [str(root)],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            with patch.object(Path, "iterdir", side_effect=OSError("read")):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_non_filesystem_page_router(self, tmp_path: Path) -> None:
        """Non-filesystem discovery routers do not run the pages-tree scan."""
        pages_root = tmp_path / "pages"
        pages_root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            with patch(
                "next.urls.RouterFactory.is_filesystem_discovery_router",
                return_value=False,
            ):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_glob_match_that_is_not_a_directory(self, tmp_path: Path) -> None:
        """Glob can match a file named like ``COMPONENTS_DIR``. That match is ignored."""
        pages_root = tmp_path / "pages"
        fake = pages_root / "seg" / "_components"
        fake.parent.mkdir(parents=True)
        fake.write_text("not a directory")
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_oserror_from_scan_directory_on_extra_root(self, tmp_path: Path) -> None:
        """OSError raised while scanning an extra component root is handled."""
        root = tmp_path / "root"
        root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [str(root)],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            with patch.object(
                ComponentScanner,
                "scan_directory",
                side_effect=OSError("scan"),
            ):
                assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()
