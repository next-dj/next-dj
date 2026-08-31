import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from next.components import (
    CachedComponentTemplateLoader,
    ComponentInfo,
    ComponentRenderer,
    ComponentScanner,
    ComponentsManager,
    ComponentTemplateLoader,
    CompositeComponentRenderer,
    DummyBackend,
    FileComponentsBackend,
    ModuleLoader,
    SimpleComponentRenderer,
    components_manager,
    get_component,
    get_component_paths_for_watch,
    load_component_template,
    register_components_folder_from_router_walk,
    render_component,
)
from next.components.manager import _on_settings_reloaded
from next.components.registry import _VISIBILITY_CACHE_MAX_SIZE
from next.components.renderers import (
    _COMPILED_TEMPLATE_CACHE_MAX_SIZE,
    TemplateSource,
    _CompiledTemplate,
    _inject_component_context,
    _merge_csrf_context,
)
from next.conf import next_framework_settings
from tests.support import (
    RaisingRootsRouter,
    RootPagesRouter,
    next_framework_settings_component_backends_list as _next_framework_settings_component_backends_list,
)


def _bump_mtime(path: Path, seconds: int = 2) -> None:
    """Move the file mtime forward so a same-second rewrite is still a change."""
    shift = seconds * 1_000_000_000
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns + shift, stat.st_mtime_ns + shift))


def _key(info: ComponentInfo) -> tuple[Path | None, Path | None]:
    """Return the compiled-cache key of a component, its pair of source files."""
    return (info.template_path, info.module_path)


def _render_body(loader: ComponentTemplateLoader, info: ComponentInfo) -> str:
    """Compile `info` through `loader` and render it against an empty context."""
    template = loader.load_template(info)
    assert template is not None
    return template.render(Context({}))


class CountingCachedLoader(CachedComponentTemplateLoader):
    """Cached loader that records how often it read a source."""

    def __init__(
        self,
        module_loader: ModuleLoader,
        maxsize: int = _COMPILED_TEMPLATE_CACHE_MAX_SIZE,
    ) -> None:
        """Start with an empty compiled cache and a zeroed read counter."""
        super().__init__(module_loader, maxsize)
        self.reads = 0

    def load_source(self, info: ComponentInfo) -> TemplateSource | None:
        self.reads += 1
        return super().load_source(info)


class SyntheticSourceLoader(CachedComponentTemplateLoader):
    """Cached loader whose bodies come from memory under a path that never stats."""

    def load_source(self, info: ComponentInfo) -> TemplateSource | None:
        return TemplateSource("<i>synthetic</i>", info.scope_root / "nowhere.djx")


class RewritingSourceLoader(CachedComponentTemplateLoader):
    """Cached loader that lets one save land while the first body is being read."""

    def __init__(self, module_loader: ModuleLoader) -> None:
        """Start with an empty compiled cache and no save queued."""
        super().__init__(module_loader)
        self.pending_save: str | None = None

    def load_source(self, info: ComponentInfo) -> TemplateSource | None:
        source = super().load_source(info)
        if self.pending_save is not None and info.template_path is not None:
            info.template_path.write_text(self.pending_save)
            _bump_mtime(info.template_path)
            self.pending_save = None
        return source


class EvictingFreshnessLoader(CachedComponentTemplateLoader):
    """Cached loader whose freshness check drops the entry, as an eviction would."""

    def _is_fresh(self, entry: _CompiledTemplate) -> bool:
        self._compiled.clear()
        return True


_STRING_IF_INVALID_TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"],
            "string_if_invalid": "MISSING",
        },
    }
]


class TestComponentsManager:
    """Tests for ComponentsManager."""

    def test_get_component_empty_when_no_config(self) -> None:
        """When ``BACKENDS`` is empty, get_component returns None."""
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.backends.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager.reload()
            assert manager.get_component("card", Path("/tmp/t.djx")) is None

    def test_collect_visible_components_merges_backends(self) -> None:
        """collect_visible_components merges from all backends, first wins."""
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.backends.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager.reload()
            assert manager.collect_visible_components(Path("/x")) == {}

    def test_reload_swallows_backend_creation_error(self) -> None:
        """An unimportable ``BACKEND`` is logged and costs only its own entry."""
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.NonexistentBackend", "OPTIONS": {}}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager.reload()
            assert len(manager._backends) == 0

    def test_template_loader_built_with_default_module_loader(self) -> None:
        """Render pipeline uses ``ComponentTemplateLoader`` wrapping ``ModuleLoader``."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.backends.next_framework_settings", mock_ns):
            mgr.reload()
        assert isinstance(mgr.template_loader, ComponentTemplateLoader)
        assert isinstance(mgr.component_renderer, ComponentRenderer)


class TestBackendsLoadedOnce:
    """Settings are read once, whatever the load leaves behind."""

    def test_empty_settings_do_not_reload_on_every_access(self) -> None:
        """An empty list is a load result, not a reason to reread settings."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list([])
        with (
            patch("next.backends.next_framework_settings", mock_ns),
            patch(
                "next.components.manager.load_backends", return_value=[]
            ) as load_backends_mock,
        ):
            manager._ensure_backends()
            manager.get_component("card", Path("/t.djx"))
            manager.collect_visible_components(Path("/t.djx"))
        assert load_backends_mock.call_count == 1

    def test_unusable_settings_do_not_reload_on_every_access(self) -> None:
        """A list nobody can load stays loaded rather than retrying per call."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.NoSuchBackend"}]
        )
        with (
            patch("next.backends.next_framework_settings", mock_ns),
            patch(
                "next.components.manager.load_backends", return_value=[]
            ) as load_backends_mock,
        ):
            manager._ensure_backends()
            manager._ensure_backends()
        assert load_backends_mock.call_count == 1

    def test_a_caller_emptying_the_list_does_not_trigger_a_reload(self) -> None:
        """The flag, not the list, answers whether settings were read."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.DummyBackend"}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            manager._ensure_backends()
            manager._backends.clear()
            manager._ensure_backends()
        assert manager._backends == []

    def test_reload_reads_settings_again(self) -> None:
        """The explicit reload is still eager, which test helpers rely on."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.DummyBackend"}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            manager._ensure_backends()
            first = manager._backends[0]
            manager.reload()
        assert manager._backends[0] is not first


class TestEntryWithoutBackendKey:
    """The manager and the watch scan resolve a ``BACKEND``-less entry alike."""

    def test_manager_falls_back_to_the_file_backend(self) -> None:
        """An entry naming no ``BACKEND`` loads the filesystem source."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"DIRS": [], "COMPONENTS_DIR": "_components"}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            manager._ensure_backends()
        assert isinstance(manager._backends[0], FileComponentsBackend)

    def test_watch_scan_falls_back_to_the_file_backend(self, tmp_path: Path) -> None:
        """The read-only scan applies the same default, so it still sees the roots."""
        root = tmp_path / "extra"
        root.mkdir()
        (root / "solo.djx").write_text("x")
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [],
                "COMPONENT_BACKENDS": [
                    {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
                ],
            }
        ):
            paths = get_component_paths_for_watch()
        next_framework_settings.reload()
        assert (root / "solo.djx").resolve() in paths


class TestBackendConstructionErrorsEscape:
    """Only configuration errors are swallowed, backend bugs are not."""

    def test_runtime_error_from_backend_init_propagates(self) -> None:
        """A user backend raising from ``__init__`` reaches the caller."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.BoomBackend"}]
        )
        with (
            patch("next.backends.next_framework_settings", mock_ns),
            pytest.raises(RuntimeError, match="boom"),
        ):
            manager._ensure_backends()


class TestSettingsReloadedIsLazy:
    """The settings receiver invalidates and leaves the rebuild to the next access."""

    def test_receiver_does_not_build_backends(self) -> None:
        """A reload costs no backend construction on its own."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.DummyBackend"}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            manager._ensure_backends()
            assert len(manager._backends) == 1
            with patch("next.components.manager.load_backends") as load_backends_mock:
                manager._invalidate()
            load_backends_mock.assert_not_called()
            assert manager._backends == []
            assert manager._loaded is False

            manager._ensure_backends()
        assert isinstance(manager._backends[0], DummyBackend)

    def test_receiver_drops_the_render_pipeline_and_walk_folders(self) -> None:
        """Cached pipeline and router-walk bookkeeping go with the backends."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.backends.next_framework_settings", mock_ns):
            manager._ensure_backends()
            assert manager.template_loader is not None
            assert manager._claim_router_walk_folder(Path("/tmp")) is True
            manager._invalidate()
        assert manager._component_renderer is None
        assert manager._walk_registered_folders == set()

    def test_reload_rebuilds_from_the_current_settings(self) -> None:
        """The public entry point swaps the backends for the configured ones."""
        manager = ComponentsManager()
        dummy = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.DummyBackend"}]
        )
        with patch("next.backends.next_framework_settings", dummy):
            manager._ensure_backends()
        assert isinstance(manager.backends[0], DummyBackend)

        file_entry = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.FileComponentsBackend", "DIRS": []}]
        )
        with patch("next.backends.next_framework_settings", file_entry):
            manager.reload()

        assert isinstance(manager.backends[0], FileComponentsBackend)

    def test_reload_drops_the_render_pipeline_and_walk_claims(
        self, tmp_path: Path
    ) -> None:
        """Rebuilding forgets which folders were claimed against the old backends."""
        manager = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.backends.next_framework_settings", mock_ns):
            manager._ensure_backends()
            loader = manager.template_loader
            assert manager._claim_router_walk_folder(tmp_path) is True
            manager.reload()
            assert manager._walk_registered_folders == set()
            assert manager.template_loader is not loader

    def test_receiver_invalidates_the_module_level_manager(self) -> None:
        """The wired receiver drops the shared manager state without a rebuild."""
        components_manager._ensure_backends()
        with patch("next.components.manager.load_backends") as load_backends_mock:
            _on_settings_reloaded()
        load_backends_mock.assert_not_called()
        assert components_manager._loaded is False


class TestRegisterComponentsFolderFromRouterWalk:
    """``register_components_folder_from_router_walk`` wiring."""

    def test_registers_scanned_components_on_backend(
        self, tmp_path: Path, installed_file_backend: FileComponentsBackend
    ) -> None:
        """Each folder is scanned into the first file components backend registry."""
        backend = installed_file_backend
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "z.djx").write_text("z")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        names = [c.name for c in backend._registry.get_all()]
        assert "z" in names

    def test_second_call_skips_same_resolved_folder(
        self, tmp_path: Path, installed_file_backend: FileComponentsBackend
    ) -> None:
        """Repeated registration for the same path is ignored."""
        backend = installed_file_backend
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "a.djx").write_text("a")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        assert len(list(backend._registry.get_all())) == 1

    def test_the_first_claim_survives_a_cold_backend_load(self, tmp_path: Path) -> None:
        # Loading the backends clears the claim set, so a claim taken before
        # that load would be forgotten and the folder registered twice.
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "a.djx").write_text("a")
        components_manager._invalidate()

        register_components_folder_from_router_walk(folder, tmp_path, "")
        register_components_folder_from_router_walk(folder, tmp_path, "")

        registered = [
            info.name
            for backend in components_manager._backends
            if isinstance(backend, FileComponentsBackend)
            for info in backend._registry
        ]
        assert registered.count("a") == 1

    def test_a_declining_backend_hands_the_folder_to_the_next_one(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A backend off the filesystem never takes a folder from a file backend."""
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "b.djx").write_text("b")
        file_backend = FileComponentsBackend(dict(min_component_config))
        manager = ComponentsManager()
        manager._backends = [DummyBackend({}), file_backend]
        manager._loaded = True

        manager.register_router_walk_folder(folder, tmp_path, "")

        assert [info.name for info in file_backend.iter_components()] == ["b"]

    def test_only_the_first_claiming_backend_registers_the_folder(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """One folder reaches exactly one backend."""
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "c.djx").write_text("c")
        first = FileComponentsBackend(dict(min_component_config))
        second = FileComponentsBackend(dict(min_component_config))
        manager = ComponentsManager()
        manager._backends = [first, second]
        manager._loaded = True

        manager.register_router_walk_folder(folder, tmp_path, "")

        assert [info.name for info in first.iter_components()] == ["c"]
        assert list(second.iter_components()) == []

    def test_a_folder_nobody_claims_is_still_claimed_once(self, tmp_path: Path) -> None:
        """The dedup set records the folder even when no backend takes it."""
        folder = tmp_path / "_components"
        folder.mkdir()
        manager = ComponentsManager()
        manager._backends = [DummyBackend({})]
        manager._loaded = True

        manager.register_router_walk_folder(folder, tmp_path, "")

        assert manager._walk_registered_folders == {folder.resolve()}

    def test_loads_component_py_when_composite_has_module(
        self, tmp_path: Path, installed_file_backend: FileComponentsBackend
    ) -> None:
        """Router walk loads ``component.py`` for composite components (coverage)."""
        backend = installed_file_backend
        comp_dir = tmp_path / "_components" / "news"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("<span>news</span>")
        (comp_dir / "component.py").write_text("# module for news\n")
        register_components_folder_from_router_walk(
            tmp_path / "_components", tmp_path, ""
        )
        infos = [i for i in backend._registry.get_all() if i.name == "news"]
        assert len(infos) == 1
        assert infos[0].module_path is not None

    def test_import_component_modules_loads_each_module_path(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """``import_component_modules`` executes ``module_loader.load`` per path."""
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
        assert backend.import_component_modules() == (comp_py,)

    def test_lazy_component_modules_skips_eager_load(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """With ``LAZY_COMPONENT_MODULES=True`` modules load on resolve, not at discovery."""
        root = tmp_path / "_components"
        comp_dir = root / "lazy_c"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.py").write_text("# lazy\n")
        (comp_dir / "component.djx").write_text("<div/>")

        config = dict(min_component_config)
        config["DIRS"] = [str(root)]

        with override_settings(
            NEXT_FRAMEWORK={
                "COMPONENT_BACKENDS": [config],
                "LAZY_COMPONENT_MODULES": True,
            }
        ):
            backend = FileComponentsBackend(config)
            with patch.object(
                backend._module_loader, "load", wraps=backend._module_loader.load
            ) as load_spy:
                backend._ensure_loaded()
                pre_resolve_calls = load_spy.call_count
                info = backend.get_component("lazy_c", comp_dir / "component.djx")
                post_resolve_calls = load_spy.call_count

        assert pre_resolve_calls == 0
        assert info is not None
        assert post_resolve_calls == 1


class TestGetComponent:
    """Tests for get_component()."""

    def test_get_component_delegates_to_manager(self) -> None:
        """get_component uses components_manager."""
        with patch("next.components.facade.components_manager") as mock_mgr:
            mock_mgr.get_component.return_value = None
            assert get_component("x", Path("/t")) is None
            mock_mgr.get_component.assert_called_once_with("x", Path("/t"))


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
        (tmp_path / "profile" / "component.djx").write_text("<div>{{ username }}</div>")
        (tmp_path / "profile" / "component.py").write_text("other = 1\n")
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
            'component = "<div>{{ x }}</div>"\n'
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
            "def render(x=''):\n    return f'<div>{x}</div>'\n"
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


class TestComponentRenderers:
    """Strategy classes and coordinator."""

    def test_component_renderer_empty_strategies(self) -> None:
        """ComponentRenderer returns empty string when no strategy matches."""
        info = ComponentInfo("x", Path("/"), "", None, None, True)
        assert ComponentRenderer([]).render(info, {}) == ""

    def test_composite_render_module_path_none_guard(self) -> None:
        """CompositeComponentRenderer.render returns '' if module_path is None."""
        loader = ModuleLoader()
        tl = ComponentTemplateLoader(loader)
        r = CompositeComponentRenderer(loader, tl)
        info = ComponentInfo("x", Path("/"), "", Path("/t.djx"), None, False)
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
            "    return HttpResponse(b'<em>ok</em>')\n"
        )
        info = ComponentInfo("hr", tmp_path, "", None, d / "component.py", False)
        out = render_component(info, {})
        assert "ok" in out

    def test_composite_template_render_injects_request(self, tmp_path: Path) -> None:
        """_render_with_template adds request to context when provided."""
        d = tmp_path / "rq"
        d.mkdir()
        (d / "component.djx").write_text("<i>{{ request.path }}</i>")
        (d / "component.py").write_text("# no render\n")
        info = ComponentInfo(
            "rq", tmp_path, "", d / "component.djx", d / "component.py", False
        )
        req = RequestFactory().get("/hello")
        html = render_component(info, {}, request=req)
        assert "/hello" in html

    def test_composite_template_render_includes_csrf_token(
        self, tmp_path: Path
    ) -> None:
        """{% csrf_token %} works in component.djx when request is passed."""
        d = tmp_path / "csrf"
        d.mkdir()
        (d / "component.djx").write_text("{% csrf_token %}")
        (d / "component.py").write_text("# no render\n")
        info = ComponentInfo(
            "csrf", tmp_path, "", d / "component.djx", d / "component.py", False
        )
        req = RequestFactory().get("/")
        html = render_component(info, {}, request=req)
        assert "csrfmiddlewaretoken" in html

    def test_merge_csrf_context_no_op_without_request(self) -> None:
        """Early return when ``request`` is None (defensive API)."""
        ctx: dict[str, object] = {}
        _merge_csrf_context(ctx, None)
        assert "csrf_token" not in ctx

    def test_merge_csrf_context_skips_when_csrf_token_present(self) -> None:
        """Do not replace an existing ``csrf_token`` (caller supplied)."""
        req = RequestFactory().get("/")
        existing = "__test_merge_csrf_existing__"
        ctx: dict[str, object] = {"csrf_token": existing}
        _merge_csrf_context(ctx, req)
        assert ctx["csrf_token"] == existing

    def test_render_with_template_returns_empty_when_no_template_string(
        self, tmp_path: Path
    ) -> None:
        """_render_with_template returns '' when template loader yields None."""
        d = tmp_path / "nt"
        d.mkdir()
        (d / "component.djx").write_text("<p>x</p>")
        (d / "component.py").write_text("# template path via djx. no render()\n")
        info = ComponentInfo(
            "nt", tmp_path, "", d / "component.djx", d / "component.py", False
        )
        loader = ModuleLoader()
        tl = ComponentTemplateLoader(loader)
        r = CompositeComponentRenderer(loader, tl)
        with patch.object(tl, "load_template", return_value=None):
            assert r._render_with_template(info, {}, None) == ""

    def test_fallback_template_none_returns_empty(self, tmp_path: Path) -> None:
        """When module load fails and template loader returns None, fallback is empty."""
        d = tmp_path / "nf"
        d.mkdir()
        (d / "component.py").write_text("syntax error (\n")
        info = ComponentInfo("nf", tmp_path, "", None, d / "component.py", False)
        r = CompositeComponentRenderer(
            ModuleLoader(), ComponentTemplateLoader(ModuleLoader())
        )
        assert r._fallback_to_template(info, {}) == ""

    def test_simple_renderer_passes_request_and_csrf_for_forms(
        self, tmp_path: Path
    ) -> None:
        """SimpleComponentRenderer adds request and csrf_token for {% csrf_token %}."""
        (tmp_path / "s.djx").write_text("<b>{% csrf_token %}</b>")
        info = ComponentInfo("s", tmp_path, "", tmp_path / "s.djx", None, True)
        tl = ComponentTemplateLoader(ModuleLoader())
        sr = SimpleComponentRenderer(tl)
        req = RequestFactory().get("/")
        html = sr.render(info, {}, request=req)
        assert "csrfmiddlewaretoken" in html
        assert "<b>" in html


class TestComponentTemplateLoaderCompilation:
    """``load_template`` on the plain loader compiles on every call."""

    def test_compiles_a_fresh_template_per_call(self, tmp_path: Path) -> None:
        """Without a cache each call parses the source again."""
        (tmp_path / "card.djx").write_text("<i>{{ x }}</i>")
        info = ComponentInfo("card", tmp_path, "", tmp_path / "card.djx", None, True)
        loader = ComponentTemplateLoader(ModuleLoader())
        first = loader.load_template(info)
        second = loader.load_template(info)
        assert first is not second
        assert first is not None
        assert first.render(Context({"x": 1})) == "<i>1</i>"

    def test_returns_none_when_the_source_is_unavailable(self) -> None:
        """A component with no readable source compiles nothing."""
        info = ComponentInfo("x", Path("/none"), "", Path("/none/x.djx"), None, True)
        assert ComponentTemplateLoader(ModuleLoader()).load_template(info) is None


class TestCachedComponentTemplateLoader:
    """Compiled templates are reused until the file they came from changes."""

    @pytest.mark.usefixtures("watched_template_edits")
    def test_second_render_reuses_the_compiled_template(self, tmp_path: Path) -> None:
        """Two renders of one component read and compile the source once."""
        (tmp_path / "card.djx").write_text("<h3>{{ title }}</h3>")
        info = ComponentInfo("card", tmp_path, "", tmp_path / "card.djx", None, True)
        loader = CountingCachedLoader(ModuleLoader())
        renderer = SimpleComponentRenderer(loader)
        with patch(
            "next.components.renderers.Template", side_effect=Template
        ) as compile_calls:
            first = renderer.render(info, {"title": "one"}, None)
            second = renderer.render(info, {"title": "two"}, None)
        assert (first, second) == ("<h3>one</h3>", "<h3>two</h3>")
        assert loader.reads == 1
        assert compile_calls.call_count == 1

    @pytest.mark.usefixtures("watched_template_edits")
    def test_editing_the_djx_body_reaches_the_next_render(self, tmp_path: Path) -> None:
        """A rewritten template renders its new body without a restart."""
        path = tmp_path / "card.djx"
        path.write_text("<h3>one</h3>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = CountingCachedLoader(ModuleLoader())
        renderer = SimpleComponentRenderer(loader)
        assert renderer.render(info, {}, None) == "<h3>one</h3>"
        path.write_text("<h3>two</h3>")
        _bump_mtime(path)
        assert renderer.render(info, {}, None) == "<h3>two</h3>"
        assert loader.reads == 2
        assert loader._compiled[_key(info)].mtime_ns == path.stat().st_mtime_ns

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_module_string_follows_its_module(self, tmp_path: Path) -> None:
        """An edited component.py expires the entry, so the body follows it.

        The module cache has no mtime of its own, so the new body arrives with
        the restart the autoreloader performs for a watched `component.py`.
        """
        module_dir = tmp_path / "mod"
        module_dir.mkdir()
        module = module_dir / "component.py"
        module.write_text('component = "<i>one</i>"\n')
        info = ComponentInfo("mod", tmp_path, "", None, module, False)
        loader = CountingCachedLoader(ModuleLoader())
        assert _render_body(loader, info) == "<i>one</i>"
        module.write_text('component = "<b>two</b>"\n')
        _bump_mtime(module)
        assert _render_body(loader, info) == "<i>one</i>"
        assert loader.reads == 2
        restarted = CachedComponentTemplateLoader(ModuleLoader())
        assert _render_body(restarted, info) == "<b>two</b>"

    @pytest.mark.usefixtures("watched_template_edits")
    def test_an_unreadable_djx_keys_the_cache_on_the_module(
        self, tmp_path: Path
    ) -> None:
        """A body read from component.py is revalidated against component.py."""
        module_dir = tmp_path / "mod"
        module_dir.mkdir()
        djx = module_dir / "component.djx"
        djx.write_bytes(b"\xff\xfe not utf-8")
        module = module_dir / "component.py"
        module.write_text('component = "<b>one</b>"\n')
        info = ComponentInfo("mod", tmp_path, "", djx, module, False)
        loader = CountingCachedLoader(ModuleLoader())
        assert _render_body(loader, info) == "<b>one</b>"
        assert loader._compiled[_key(info)].source_path == module
        module.write_text('component = "<b>two</b>"\n')
        _bump_mtime(module)
        assert _render_body(loader, info) == "<b>one</b>"
        assert loader.reads == 2
        restarted = CachedComponentTemplateLoader(ModuleLoader())
        assert _render_body(restarted, info) == "<b>two</b>"

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_missing_djx_caches_the_module_string(self, tmp_path: Path) -> None:
        """A declared but absent .djx leaves the module body cached, not re-read."""
        module_dir = tmp_path / "mod"
        module_dir.mkdir()
        module = module_dir / "component.py"
        module.write_text('component = "<i>from module</i>"\n')
        info = ComponentInfo(
            "mod", tmp_path, "", module_dir / "component.djx", module, False
        )
        loader = CountingCachedLoader(ModuleLoader())
        renderer = SimpleComponentRenderer(loader)
        assert renderer.render(info, {}, None) == "<i>from module</i>"
        assert renderer.render(info, {}, None) == "<i>from module</i>"
        assert loader.reads == 1
        assert loader._compiled[_key(info)].source_path == module

    def test_module_as_template_path_keys_the_cache_by_the_module(
        self, tmp_path: Path
    ) -> None:
        """A component.py standing in for the template still keys on itself."""
        module_dir = tmp_path / "mod"
        module_dir.mkdir()
        module = module_dir / "component.py"
        module.write_text('component = "<i>x</i>"\n')
        info = ComponentInfo("mod", tmp_path, "", module, module, False)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert loader.load_template(info) is not None
        assert loader._compiled[_key(info)].source_path == module

    @pytest.mark.usefixtures("watched_template_edits")
    def test_deleted_file_renders_empty_and_drops_the_entry(
        self, tmp_path: Path
    ) -> None:
        """A template removed between renders falls back to an empty render."""
        path = tmp_path / "card.djx"
        path.write_text("<h3>one</h3>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        renderer = SimpleComponentRenderer(loader)
        assert renderer.render(info, {}, None) == "<h3>one</h3>"
        path.unlink()
        assert renderer.render(info, {}, None) == ""
        assert loader._compiled == {}

    @pytest.mark.usefixtures("watched_template_edits")
    def test_unreadable_source_drops_a_stale_entry(self, tmp_path: Path) -> None:
        """A body that stops decoding invalidates the compiled template."""
        path = tmp_path / "card.djx"
        path.write_text("<h3>one</h3>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert loader.load_template(info) is not None
        path.write_bytes(b"\xff\xfe broken")
        _bump_mtime(path)
        assert loader.load_template(info) is None
        assert loader._compiled == {}

    def test_component_without_files_caches_nothing(self, tmp_path: Path) -> None:
        """No source file means no mtime to key on, so nothing is stored."""
        info = ComponentInfo("ghost", tmp_path, "", None, None, True)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert loader.load_template(info) is None
        assert loader._compiled == {}

    def test_a_source_that_does_not_stat_is_compiled_but_not_cached(
        self, tmp_path: Path
    ) -> None:
        """A body under a path with no mtime has nothing to revalidate against."""
        info = ComponentInfo("x", tmp_path, "", None, None, True)
        loader = SyntheticSourceLoader(ModuleLoader())
        assert _render_body(loader, info) == "<i>synthetic</i>"
        assert loader._compiled == {}

    def test_a_full_cache_evicts_the_least_recently_used_entry(
        self, tmp_path: Path
    ) -> None:
        """Eviction follows use order and never hands out another component's body."""
        infos = []
        for name in ("a", "b", "c"):
            (tmp_path / f"{name}.djx").write_text(f"<i>{name}</i>")
            infos.append(
                ComponentInfo(name, tmp_path, "", tmp_path / f"{name}.djx", None, True)
            )
        first, second, third = infos
        loader = CachedComponentTemplateLoader(ModuleLoader(), maxsize=2)
        assert _render_body(loader, first) == "<i>a</i>"
        assert _render_body(loader, second) == "<i>b</i>"
        assert _render_body(loader, first) == "<i>a</i>"
        assert _render_body(loader, third) == "<i>c</i>"
        assert list(loader._compiled) == [_key(first), _key(third)]
        assert _render_body(loader, second) == "<i>b</i>"

    def test_clear_drops_every_compiled_template(self, tmp_path: Path) -> None:
        """The seam a test uses after rewriting a component on disk."""
        path = tmp_path / "card.djx"
        path.write_text("<i>one</i>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert _render_body(loader, info) == "<i>one</i>"
        loader.clear()
        assert loader._compiled == {}
        path.write_text("<i>two</i>")
        assert _render_body(loader, info) == "<i>two</i>"

    def test_the_default_bound_matches_the_other_component_caches(self) -> None:
        """One bound covers every path-keyed component cache in the process."""
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert loader._maxsize == _VISIBILITY_CACHE_MAX_SIZE

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_save_during_the_read_does_not_hide_the_edit(
        self, tmp_path: Path
    ) -> None:
        """A rewrite landing between the stat and the read still expires the entry."""
        path = tmp_path / "card.djx"
        path.write_text("<i>one</i>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = RewritingSourceLoader(ModuleLoader())
        loader.pending_save = "<i>two</i>"
        assert _render_body(loader, info) == "<i>one</i>"
        assert _render_body(loader, info) == "<i>two</i>"

    @pytest.mark.usefixtures("watched_template_edits")
    def test_an_entry_dropped_while_it_is_checked_still_renders(
        self, tmp_path: Path
    ) -> None:
        """An eviction racing the freshness check costs no render."""
        path = tmp_path / "card.djx"
        path.write_text("<i>one</i>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = EvictingFreshnessLoader(ModuleLoader())
        assert _render_body(loader, info) == "<i>one</i>"
        assert _render_body(loader, info) == "<i>one</i>"
        assert loader._compiled == {}

    def test_marking_a_dropped_key_leaves_the_cache_alone(self) -> None:
        """The reorder a concurrent eviction has already outrun does nothing."""
        loader = CachedComponentTemplateLoader(ModuleLoader())
        loader._mark_used((Path("/gone/card.djx"), None))
        assert loader._compiled == {}

    def test_use_order_is_tracked_only_once_the_cache_is_full(
        self, tmp_path: Path
    ) -> None:
        """A cache with room to spare skips the reorder every hit would pay."""
        path = tmp_path / "card.djx"
        path.write_text("<i>one</i>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert _render_body(loader, info) == "<i>one</i>"
        with patch.object(loader, "_mark_used") as reorder:
            assert _render_body(loader, info) == "<i>one</i>"
        assert reorder.call_count == 0


class TestTemplatesSettingInvalidation:
    """A `TEMPLATES` change reaches a component whose template is already compiled."""

    def test_an_engine_change_drops_the_compiled_templates(
        self, tmp_path: Path
    ) -> None:
        """The new engine renders the component the old one had already compiled."""
        path = tmp_path / "card.djx"
        path.write_text("<i>{{ missing }}</i>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = components_manager.template_loader
        assert SimpleComponentRenderer(loader).render(info, {}, None) == "<i></i>"
        with override_settings(TEMPLATES=_STRING_IF_INVALID_TEMPLATES):
            overridden = components_manager.template_loader
            assert overridden is not loader
            rendered = SimpleComponentRenderer(overridden).render(info, {}, None)
            assert rendered == "<i>MISSING</i>"
        assert components_manager.template_loader is not overridden


class TestRevalidationFollowsWatchedEdits:
    """Sources are stat-ed only where an edit can reach a render."""

    @pytest.mark.usefixtures("watched_template_edits")
    def test_edits_are_picked_up_only_where_they_are_watched(
        self, tmp_path: Path
    ) -> None:
        """An unwatched process reuses the entry, and the watch takes the edit."""
        path = tmp_path / "card.djx"
        path.write_text("<h3>one</h3>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        loader = CountingCachedLoader(ModuleLoader())
        with override_settings(DEBUG=False):
            assert _render_body(loader, info) == "<h3>one</h3>"
            path.write_text("<h3>two</h3>")
            _bump_mtime(path)
            assert _render_body(loader, info) == "<h3>one</h3>"
            assert loader.reads == 1
        assert _render_body(loader, info) == "<h3>two</h3>"
        assert loader.reads == 2


class TestRenderPipelineCaching:
    """The pipeline `components_manager` builds caches compilation end to end."""

    @pytest.mark.parametrize("debug", [True, False], ids=["watched", "unwatched"])
    def test_a_repeated_render_neither_reads_nor_compiles(
        self, tmp_path: Path, *, debug: bool
    ) -> None:
        """A warm render through render_component costs no read and no parse."""
        path = tmp_path / "card.djx"
        path.write_text("<h3>{{ title }}</h3>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        with override_settings(DEBUG=debug):
            assert render_component(info, {"title": "one"}) == "<h3>one</h3>"
            with (
                patch(
                    "next.components.renderers.Template", side_effect=Template
                ) as compiles,
                patch.object(
                    Path, "read_text", autospec=True, side_effect=Path.read_text
                ) as reads,
                patch.object(
                    Path, "stat", autospec=True, side_effect=Path.stat
                ) as stats,
            ):
                html = render_component(info, {"title": "two"})
        assert html == "<h3>two</h3>"
        assert compiles.call_count == 0
        assert reads.call_count == 0
        assert stats.call_count == (1 if debug else 0)

    @pytest.mark.usefixtures("watched_template_edits")
    def test_an_edited_djx_reaches_the_next_render(self, tmp_path: Path) -> None:
        """The live pipeline picks up a rewritten body while edits are watched."""
        path = tmp_path / "card.djx"
        path.write_text("<h3>one</h3>")
        info = ComponentInfo("card", tmp_path, "", path, None, True)
        assert render_component(info, {}) == "<h3>one</h3>"
        path.write_text("<h3>two</h3>")
        _bump_mtime(path)
        assert render_component(info, {}) == "<h3>two</h3>"


class TestInjectComponentContext:
    """_inject_component_context early exits."""

    def test_no_op_when_no_module_path(self) -> None:
        """When module_path is None, nothing is merged."""
        info = ComponentInfo("s", Path("/"), "", Path("/t.djx"), None, True)
        data: dict[str, object] = {"keep": 1}
        _inject_component_context(info, data, None)
        assert data == {"keep": 1}


class TestGetComponentPathsForWatch:
    """``get_component_paths_for_watch`` mirrors discovery without mutating managers."""

    def test_empty_when_backend_settings_not_lists(self) -> None:
        """Return empty sets when ``*_BACKENDS`` settings are not lists."""
        mock_nf = SimpleNamespace(
            PAGE_BACKENDS="not-a-list", COMPONENT_BACKENDS="not-a-list"
        )
        with patch("next.backends.next_framework_settings", mock_nf):
            assert get_component_paths_for_watch() == set()

    def test_collects_composite_under_pages_tree(self, tmp_path: Path) -> None:
        """Paths include ``component.djx`` under ``COMPONENTS_DIR`` in a pages tree."""
        pages_root = tmp_path / "pages"
        comp_dir = pages_root / "_components" / "widget"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("<span/>")
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    }
                ],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
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
                "PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    }
                ],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            paths = get_component_paths_for_watch()
        next_framework_settings.reload()
        assert (comp_dir / "component.py").resolve() in paths

    def test_skips_non_dict_page_config(self) -> None:
        """Non-dict ``PAGE_BACKENDS`` entries are ignored."""
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": ["not-dict"], "COMPONENT_BACKENDS": []}
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_non_dict_component_config(self) -> None:
        """Non-dict ``COMPONENT_BACKENDS`` entries are ignored."""
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [], "COMPONENT_BACKENDS": ["bad"]}
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_collects_simple_djx_in_extra_root(self, tmp_path: Path) -> None:
        """Extra component root picks up top-level ``.djx`` simple components."""
        root = tmp_path / "extra"
        root.mkdir()
        (root / "solo.djx").write_text("x")
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [str(root)],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            paths = get_component_paths_for_watch()
        next_framework_settings.reload()
        assert (root / "solo.djx").resolve() in paths

    def test_swallows_page_backend_create_error(self, tmp_path: Path) -> None:
        """Invalid page backend config is skipped after logging."""
        pages_root = tmp_path / "pages"
        pages_root.mkdir()
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [
                        {
                            "BACKEND": "next.urls.FileRouterBackend",
                            "PAGES_DIR": "pages",
                            "APP_DIRS": False,
                            "DIRS": [str(pages_root)],
                            "OPTIONS": {},
                        }
                    ],
                    "COMPONENT_BACKENDS": [],
                }
            ),
            patch(
                "next.urls.RouterFactory.create_backend",
                side_effect=ValueError("bad config"),
            ),
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_glob_oserror_swallowed_for_pages_scan(self, tmp_path: Path) -> None:
        """OSError from ``Path.glob`` while finding component dirs is handled."""
        pages_root = tmp_path / "pages"
        pages_root.mkdir()
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [
                        {
                            "BACKEND": "next.urls.FileRouterBackend",
                            "PAGES_DIR": "pages",
                            "APP_DIRS": False,
                            "DIRS": [str(pages_root)],
                            "OPTIONS": {},
                        }
                    ],
                    "COMPONENT_BACKENDS": [
                        {
                            "BACKEND": "next.components.FileComponentsBackend",
                            "DIRS": [],
                            "COMPONENTS_DIR": "_components",
                        }
                    ],
                }
            ),
            patch.object(Path, "glob", side_effect=OSError("glob fail")),
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_relative_to_valueerror_skips_component_dir(self, tmp_path: Path) -> None:
        """When ``relative_to`` fails, that ``_components`` folder is skipped."""
        pages_root = tmp_path / "pages"
        comp_dir = pages_root / "_components" / "w"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("x")
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [
                        {
                            "BACKEND": "next.urls.FileRouterBackend",
                            "PAGES_DIR": "pages",
                            "APP_DIRS": False,
                            "DIRS": [str(pages_root)],
                            "OPTIONS": {},
                        }
                    ],
                    "COMPONENT_BACKENDS": [
                        {
                            "BACKEND": "next.components.FileComponentsBackend",
                            "DIRS": [],
                            "COMPONENTS_DIR": "_components",
                        }
                    ],
                }
            ),
            patch.object(Path, "relative_to", side_effect=ValueError("outside")),
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_swallows_component_backend_resolution_error(self) -> None:
        """An entry whose ``BACKEND`` does not import is logged and skipped."""
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.NoSuchBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_non_file_component_backend(self) -> None:
        """Non-``FileComponentsBackend`` entries do not contribute paths."""
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.DummyBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_oserror_scanning_extra_root(self, tmp_path: Path) -> None:
        """OSError when listing an extra component root is handled."""
        root = tmp_path / "r"
        root.mkdir()
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [],
                    "COMPONENT_BACKENDS": [
                        {
                            "BACKEND": "next.components.FileComponentsBackend",
                            "DIRS": [str(root)],
                            "COMPONENTS_DIR": "_components",
                        }
                    ],
                }
            ),
            patch.object(Path, "iterdir", side_effect=OSError("read")),
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_survives_a_router_whose_tree_listing_raises(self, tmp_path: Path) -> None:
        """`collectstatic` reaches this scan, so a raising router costs its paths only."""
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [{"BACKEND": "broken.Backend"}],
                    "COMPONENT_BACKENDS": [
                        {
                            "BACKEND": "next.components.FileComponentsBackend",
                            "DIRS": [],
                            "COMPONENTS_DIR": "_components",
                        }
                    ],
                }
            ),
            patch(
                "next.urls.RouterFactory.create_backend",
                return_value=RaisingRootsRouter(),
            ),
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_skips_router_without_a_components_folder(self, tmp_path: Path) -> None:
        """A router that registers no component folder runs no pages-tree scan."""
        pages_root = tmp_path / "pages"
        comp_dir = pages_root / "_components" / "widget"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("<span/>")
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [{"BACKEND": "elsewhere.Backend"}],
                    "COMPONENT_BACKENDS": [
                        {
                            "BACKEND": "next.components.FileComponentsBackend",
                            "DIRS": [],
                            "COMPONENTS_DIR": "_components",
                        }
                    ],
                }
            ),
            patch(
                "next.urls.RouterFactory.create_backend",
                return_value=RootPagesRouter([pages_root]),
            ),
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
                "PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(pages_root)],
                        "OPTIONS": {},
                    }
                ],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()

    def test_oserror_from_scan_directory_on_extra_root(self, tmp_path: Path) -> None:
        """OSError raised while scanning an extra component root is handled."""
        root = tmp_path / "root"
        root.mkdir()
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [],
                    "COMPONENT_BACKENDS": [
                        {
                            "BACKEND": "next.components.FileComponentsBackend",
                            "DIRS": [str(root)],
                            "COMPONENTS_DIR": "_components",
                        }
                    ],
                }
            ),
            patch.object(
                ComponentScanner, "scan_directory", side_effect=OSError("scan")
            ),
        ):
            assert get_component_paths_for_watch() == set()
        next_framework_settings.reload()
