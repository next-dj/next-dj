import importlib.util
import inspect
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.template import Context, Template
from django.template.base import TemplateSyntaxError
from django.test import RequestFactory, override_settings

import next.components as next_components_mod
from next.checks import (
    check_component_py_no_pages_context,
    check_cross_root_component_name_conflicts,
    check_duplicate_component_names,
)
from next.components import (
    ComponentContextManager,
    ComponentContextRegistry,
    ComponentInfo,
    ComponentRegistry,
    ComponentRenderer,
    ComponentScanner,
    ComponentsFactory,
    ComponentsManager,
    ComponentTemplateLoader,
    ComponentVisibilityResolver,
    CompositeComponentRenderer,
    DummyBackend,
    FileComponentsBackend,
    ModuleCache,
    ModuleLoader,
    SimpleComponentRenderer,
    _inject_component_context,
    component,
    component_extra_roots_from_config,
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
    next_framework_settings_for_checks_backends_value as _next_framework_settings_for_checks_backends_value,
    patch_checks_components_manager,
)


_MIN_FILE_COMPONENTS: dict[str, object] = {
    "DIRS": [],
    "COMPONENTS_DIR": "_components",
}


class TestComponentsModuleExports:
    """``next.components`` public API surface."""

    def test_all_names_exist_on_module(self) -> None:
        """Every name in ``__all__`` exists on the module."""
        for name in next_components_mod.__all__:
            assert hasattr(next_components_mod, name)


class TestComponentInfo:
    """Tests for ComponentInfo dataclass."""

    def test_component_info_simple(self) -> None:
        """Simple component has template_path and no module_path."""
        info = ComponentInfo(
            name="card",
            scope_root=Path("/app/pages"),
            scope_relative="",
            template_path=Path("/app/pages/_components/card.djx"),
            module_path=None,
            is_simple=True,
        )
        assert info.name == "card"
        assert info.is_simple
        assert info.template_path is not None
        assert info.module_path is None


class TestFileComponentsBackend:
    """Tests for FileComponentsBackend discovery and resolution."""

    def test_collect_visible_empty_when_no_roots(self) -> None:
        """With empty ``DIRS`` and no registry data, no components are visible."""
        backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        visible = backend.collect_visible_components(Path("/tmp/some/template.djx"))
        assert visible == {}

    def test_get_component_returns_none_when_empty(self) -> None:
        """get_component returns None when no backends have it."""
        backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        assert backend.get_component("card", Path("/tmp/template.djx")) is None

    def test_discover_in_component_root_simple(self, tmp_path: Path) -> None:
        """Root component dir: .djx files are discovered as simple components."""
        (tmp_path / "header.djx").write_text("<header>Hi</header>")
        backend = FileComponentsBackend(
            {**_MIN_FILE_COMPONENTS, "DIRS": [str(tmp_path)]},
        )
        backend._ensure_loaded()
        assert len(backend._registry) == 1
        components = list(backend._registry)
        assert len(components) == 1
        info = components[0]
        assert info.name == "header"
        assert info.scope_relative == ""
        assert info.is_simple
        assert info.template_path == tmp_path / "header.djx"

    def test_discover_in_component_root_composite(self, tmp_path: Path) -> None:
        """Root component dir: subdir with component.djx is composite."""
        (tmp_path / "profile").mkdir()
        (tmp_path / "profile" / "component.djx").write_text("<div>profile</div>")
        backend = FileComponentsBackend(
            {**_MIN_FILE_COMPONENTS, "DIRS": [str(tmp_path)]},
        )
        backend._ensure_loaded()
        assert len(backend._registry) == 1
        components = list(backend._registry)
        info = components[0]
        assert info.name == "profile"
        assert not info.is_simple
        assert info.template_path == tmp_path / "profile" / "component.djx"

    def test_string_base_dir_normalized_for_discovery(self, tmp_path: Path) -> None:
        """``BASE_DIR`` as str is converted to ``Path`` for ``DIRS`` resolution."""
        (tmp_path / "nest").mkdir()
        with patch("next.filesystem.settings") as mock_settings:
            mock_settings.BASE_DIR = str(tmp_path)
            roots = component_extra_roots_from_config({"DIRS": ["nest"]})
        assert roots == [(tmp_path / "nest").resolve()]

    def test_file_components_backend_normalizes_string_base_dir(
        self, tmp_path: Path
    ) -> None:
        """``BASE_DIR`` as str is normalized when resolving ``DIRS``."""
        (tmp_path / "c").mkdir()
        with patch("next.filesystem.settings") as mock_settings:
            mock_settings.BASE_DIR = str(tmp_path)
            FileComponentsBackend({**_MIN_FILE_COMPONENTS, "DIRS": ["c"]})

    def test_discover_component_roots_from_dirs(self, tmp_path: Path) -> None:
        """``component_extra_roots_from_config`` returns existing paths from ``DIRS``."""
        assert component_extra_roots_from_config({"DIRS": ["/nonexistent/root"]}) == []

        roots = component_extra_roots_from_config({"DIRS": [str(tmp_path)]})
        assert len(roots) == 1
        assert roots[0] == tmp_path.resolve()

    def test_root_components_visible_from_any_path(self, tmp_path: Path) -> None:
        """Root component roots are visible from any template path."""
        (tmp_path / "global.djx").write_text("<div>global</div>")
        backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))

        # Register component directly in the new registry
        info = ComponentInfo(
            name="global",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "global.djx",
            module_path=None,
            is_simple=True,
        )
        backend._registry.register(info)
        backend._registry.mark_as_root(tmp_path)
        backend._loaded = True

        visible = backend.collect_visible_components(Path("/other/path/template.djx"))
        assert "global" in visible

    def test_visible_from_template_under_scope(self, tmp_path: Path) -> None:
        """Component in scope_relative is visible from template under that path."""
        comp_dir = tmp_path / "pages" / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "card.djx").write_text("<div>card</div>")
        backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))

        # Register component directly in the new registry
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path / "pages",
            scope_relative="about",
            template_path=comp_dir / "card.djx",
            module_path=None,
            is_simple=True,
        )
        backend._registry.register(info)
        backend._loaded = True

        template_path = tmp_path / "pages" / "about" / "team" / "template.djx"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        visible = backend.collect_visible_components(template_path)
        assert "card" in visible
        assert visible["card"].name == "card"


class TestComponentsFactory:
    """Tests for ComponentsFactory."""

    def test_create_backend_file_default(self) -> None:
        """Create FileComponentsBackend with merged-style keys."""
        config = {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "_components",
        }
        backend = ComponentsFactory.create_backend(config)
        assert isinstance(backend, FileComponentsBackend)
        assert backend.components_dir == "_components"

    def test_create_backend_file_with_component_dirs(self) -> None:
        """Create FileComponentsBackend with ``COMPONENTS_DIR`` and empty ``DIRS``."""
        config = {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "components",
        }
        backend = ComponentsFactory.create_backend(config)
        assert isinstance(backend, FileComponentsBackend)
        assert backend.components_dir == "components"
        assert backend._extra_component_roots == []

    def test_create_backend_unknown_raises(self) -> None:
        """Unknown backend class path raises ImportError."""
        with pytest.raises(ImportError):
            ComponentsFactory.create_backend(
                {"BACKEND": "next.components.UnknownBackend"}
            )


class TestComponentsManager:
    """Tests for ComponentsManager."""

    def test_get_component_empty_when_no_config(self) -> None:
        """When ``BACKENDS`` is empty, get_component returns None."""
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.components.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager._reload_config()
            assert manager.get_component("card", Path("/tmp/t.djx")) is None

    def test_collect_visible_components_merges_backends(self) -> None:
        """collect_visible_components merges from all backends, first wins."""
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.components.next_framework_settings", mock_ns):
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
        with patch("next.components.next_framework_settings", mock_ns):
            manager = ComponentsManager()
            manager._reload_config()
            assert len(manager._backends) == 0

    def test_template_loader_built_with_default_module_loader(self) -> None:
        """Render pipeline uses ``ComponentTemplateLoader`` wrapping ``ModuleLoader``."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list([])
        with patch("next.components.next_framework_settings", mock_ns):
            mgr._reload_config()
        assert isinstance(mgr.template_loader, ComponentTemplateLoader)
        assert isinstance(mgr.component_renderer, ComponentRenderer)


class TestRegisterComponentsFolderFromRouterWalk:
    """``register_components_folder_from_router_walk`` wiring."""

    def test_registers_scanned_components_on_backend(self, tmp_path: Path) -> None:
        """Each folder is scanned into the first file components backend registry."""
        components_manager._walk_registered_folders.clear()
        components_manager._backends.clear()
        backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        components_manager._backends.append(backend)
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "z.djx").write_text("z")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        names = [c.name for c in backend._registry.get_all()]
        assert "z" in names

    def test_second_call_skips_same_resolved_folder(self, tmp_path: Path) -> None:
        """Repeated registration for the same path is ignored."""
        components_manager._walk_registered_folders.clear()
        components_manager._backends.clear()
        backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        components_manager._backends.append(backend)
        folder = tmp_path / "_components"
        folder.mkdir()
        (folder / "a.djx").write_text("a")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        register_components_folder_from_router_walk(folder, tmp_path, "")
        assert len(list(backend._registry.get_all())) == 1


class TestGetComponent:
    """Tests for get_component()."""

    def test_get_component_delegates_to_manager(self) -> None:
        """get_component uses components_manager."""
        with patch("next.components.components_manager") as mock_mgr:
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


class TestChecks:
    """Tests for component-related Django checks."""

    def test_check_duplicate_component_names_empty_when_no_config(self) -> None:
        """check_duplicate_component_names returns [] when backends is not a list."""
        mock_ns = _next_framework_settings_for_checks_backends_value(None)
        with patch("next.checks.next_framework_settings", mock_ns):
            assert check_duplicate_component_names() == []

    def test_check_component_py_no_pages_context_empty_when_no_config(self) -> None:
        """check_component_py_no_pages_context returns [] when backends is not a list."""
        mock_ns = _next_framework_settings_for_checks_backends_value(None)
        with patch("next.checks.next_framework_settings", mock_ns):
            assert check_component_py_no_pages_context() == []

    def test_check_duplicate_component_names_reports_duplicate(
        self, tmp_path: Path
    ) -> None:
        """check_duplicate_component_names reports when same name in same scope."""
        (tmp_path / "a.djx").write_text("a")
        (tmp_path / "b.djx").write_text("b")
        fake_backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))

        # Register duplicates using new registry
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", tmp_path / "a.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", tmp_path / "b.djx", None, True)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_duplicate_component_names()
        assert any(e.id == "next.E020" for e in errors)

    def test_check_duplicate_component_names_root_and_nested_scope(
        self, tmp_path: Path
    ) -> None:
        """Same name at root route scope and under a nested route is rejected."""
        root = tmp_path.resolve()
        (tmp_path / "a.djx").write_text("a")
        (tmp_path / "b.djx").write_text("b")
        fake_backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        fake_backend._registry.register(
            ComponentInfo("card", root, "", tmp_path / "a.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("card", root, "blog", tmp_path / "b.djx", None, True)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            errors = check_duplicate_component_names()
        assert any(e.id == "next.E020" for e in errors)

    def test_check_cross_root_component_name_conflicts_empty_single_tree(
        self, tmp_path: Path
    ) -> None:
        """One page tree can reuse a name only under different route scopes."""
        root = tmp_path.resolve()
        (tmp_path / "a.djx").write_text("a")
        fake_backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        fake_backend._registry.register(
            ComponentInfo("card", root, "", tmp_path / "a.djx", None, True)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            assert check_cross_root_component_name_conflicts() == []

    def test_check_cross_root_component_name_conflicts_reports(
        self, tmp_path: Path
    ) -> None:
        """Same root-level name on two page trees is rejected."""
        custom = (tmp_path / "custom").resolve()
        pages = (tmp_path / "pages").resolve()
        custom.mkdir()
        pages.mkdir()
        (custom / "c.djx").write_text("x")
        (pages / "c.djx").write_text("y")
        fake_backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))
        fake_backend._registry.register(
            ComponentInfo("hero", custom, "", custom / "c.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("hero", pages, "", pages / "c.djx", None, True)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            errors = check_cross_root_component_name_conflicts()
        assert any(e.id == "next.E034" for e in errors)

    def test_check_component_py_no_pages_context_reports_import(
        self, tmp_path: Path
    ) -> None:
        """check_component_py_no_pages_context reports when component.py imports context from next.pages."""
        (tmp_path / "component.py").write_text("from next.pages import context\n")
        fake_backend = FileComponentsBackend(dict(_MIN_FILE_COMPONENTS))

        # Register component using new registry
        fake_backend._registry.register(
            ComponentInfo(
                "bad",
                tmp_path,
                "",
                None,
                tmp_path / "component.py",
                False,
            )
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_component_py_no_pages_context()
        assert any(e.id == "next.E021" for e in errors)


class TestComponentContextManager:
    """Tests for ComponentContextManager."""

    def test_component_context_injected_on_render(self, tmp_path: Path) -> None:
        """When component has context registered, _inject_component_context adds it to render."""
        (tmp_path / "comp").mkdir()
        (tmp_path / "comp" / "component.djx").write_text("<span>{{ injected }}</span>")
        (tmp_path / "comp" / "component.py").write_text("# empty\n")
        component._registry.register(
            tmp_path / "comp" / "component.py",
            None,
            lambda: {"injected": "from_context"},
        )
        info = ComponentInfo(
            name="comp",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "comp" / "component.djx",
            module_path=tmp_path / "comp" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {})
        assert "from_context" in html

    def test_component_context_keyed_injected_on_render(self, tmp_path: Path) -> None:
        """Keyed context is injected as context_data[key]."""
        (tmp_path / "k").mkdir()
        (tmp_path / "k" / "component.djx").write_text("<span>{{ count }}</span>")
        (tmp_path / "k" / "component.py").write_text("# empty\n")
        component._registry.register(
            tmp_path / "k" / "component.py",
            "count",
            lambda: 42,
        )
        info = ComponentInfo(
            name="k",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "k" / "component.djx",
            module_path=tmp_path / "k" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {})
        assert "42" in html

    def test_register_and_get_registry(self) -> None:
        """Context can be registered for a path and retrieved."""
        path = Path("/fake/app/pages/_components/stats/component.py")

        def my_context(request: object) -> dict:
            return {"count": 1}

        component._registry.register(path, "data", my_context)
        funcs = component.get_functions(path)
        assert any(cf.key == "data" for cf in funcs)
        func = next(cf.func for cf in funcs if cf.key == "data")
        assert func(None) == {"count": 1}

    def test_register_reserved_di_key_raises(self) -> None:
        """Cannot use names reserved for resolve_dependencies."""
        path = Path("/fake/app/pages/_components/x/component.py")
        with pytest.raises(ValueError, match="reserved for dependency injection"):
            component._registry.register(path, "request", lambda: None)

    def test_register_duplicate_key_raises(self) -> None:
        """Same context key cannot be registered twice for one component.py."""
        path = Path("/fake/app/pages/_components/y/component.py")

        def f1() -> int:
            return 1

        def f2() -> int:
            return 2

        component._registry.register(path, "slot", f1)
        with pytest.raises(ValueError, match="Duplicate component context"):
            component._registry.register(path, "slot", f2)

    def test_register_same_callable_twice_ok(self) -> None:
        """Re-registering the same function does not raise."""
        path = Path("/fake/app/pages/_components/z/component.py")

        def stable() -> int:
            return 1

        component._registry.register(path, "x", stable)
        component._registry.register(path, "x", stable)


class TestComponentTag:
    """Tests for {% component %} and {% endcomponent %} tags."""

    def test_component_tag_requires_name(self) -> None:
        """{% component %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="component name"):
            Template("{% load components %}{% component %}")

    def test_component_tag_requires_quoted_name(self) -> None:
        """{% component %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted"):
            Template('{% load components %}{% component "" %}{% endcomponent %}')

    def test_component_tag_renders_empty_without_path_in_context(self) -> None:
        """When current_template_path is missing, component renders empty."""
        t = Template(
            '{% load components %}{% component "card" title="Hi" %}{% endcomponent %}'
        )
        result = t.render(Context({}))
        assert result == ""

    def test_component_tag_renders_empty_when_path_not_str_or_path(self) -> None:
        """When current_template_path is not str/Path (e.g. int), component renders empty."""
        t = Template('{% load components %}{% component "card" %}{% endcomponent %}')
        result = t.render(Context({"current_template_path": 42}))
        assert result == ""

    def test_component_tag_renders_empty_when_component_not_found(self) -> None:
        """When component is not resolved, renders empty."""
        t = Template(
            '{% load components %}{% component "nonexistent" %}{% endcomponent %}'
        )
        with patch("next.templatetags.components.get_component", return_value=None):
            result = t.render(
                Context({"current_template_path": "/app/pages/home/template.djx"}),
            )
        assert result == ""

    def test_component_tag_renders_component_when_found(self, tmp_path: Path) -> None:
        """When component is found and path in context, renders template."""
        (tmp_path / "card.djx").write_text('<div class="card">{{ title }}</div>')
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="card",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "card.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                "{% load components %}"
                '{% component "card" title="Hello" %}{% endcomponent %}'
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "template.djx")}),
            )
        assert 'class="card"' in result
        assert "Hello" in result

    def test_component_tag_accepts_path_object_in_context(self, tmp_path: Path) -> None:
        """current_template_path in context can be a Path object."""
        with patch.object(
            components_manager,
            "get_component",
            return_value=None,
        ):
            t = Template('{% load components %}{% component "c" %}{% endcomponent %}')
            t.render(Context({"current_template_path": tmp_path / "t.djx"}))

    def test_component_tag_with_slots_passes_slot_content(self, tmp_path: Path) -> None:
        """When component body has {% slot %}, content is passed to component."""
        (tmp_path / "box.djx").write_text(
            '<div class="box">{{ slot_image }} {{ children }}</div>',
        )
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="box",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "box.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                "{% load components %}"
                '{% component "box" %}'
                '{% slot "image" %}<img src="x"/>{% endslot %}'
                "kids"
                "{% endcomponent %}"
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "template.djx")}),
            )
        assert "slot_image" in result or "<img" in result
        assert "kids" in result

    def test_component_tag_with_props(self, tmp_path: Path) -> None:
        """Component tag parses key=val props."""
        (tmp_path / "card.djx").write_text("<h1>{{ title }}</h1>")
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="card",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "card.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                "{% load components %}"
                '{% component "card" title="My Title" %}{% endcomponent %}'
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "t.djx")}),
            )
        assert "My Title" in result

    def test_component_tag_ignores_token_without_equals(self, tmp_path: Path) -> None:
        """Extra words without ``=`` in the opening tag are skipped for props."""
        (tmp_path / "card.djx").write_text("<h1>{{ title }}</h1>")
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="card",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "card.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                "{% load components %}"
                '{% component "card" orphan title="Kept" %}{% endcomponent %}'
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "t.djx")}),
            )
        assert "Kept" in result


class TestSlotTag:
    """Tests for {% slot %} and {% endslot %} tags."""

    def test_slot_tag_requires_name(self) -> None:
        """{% slot %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="slot"):
            Template(
                "{% load components %}"
                '{% component "c" %}{% slot %}{% endslot %}{% endcomponent %}'
            )

    def test_slot_tag_requires_exactly_one_arg(self) -> None:
        """{% slot %} with 0 or 3 args raises."""
        with pytest.raises(TemplateSyntaxError, match="exactly one"):
            Template(
                "{% load components %}"
                '{% component "c" %}{% slot "a" "b" %}{% endslot %}{% endcomponent %}'
            )

    def test_slot_tag_requires_quoted_name(self) -> None:
        """{% slot %} with empty name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template(
                "{% load components %}"
                '{% component "c" %}{% slot "" %}{% endslot %}{% endcomponent %}'
            )

    def test_slot_tag_parses(self) -> None:
        r"""{% slot "x" %} ... {% endslot %} parses inside component."""
        t = Template(
            "{% load components %}"
            '{% component "c" %}'
            '{% slot "image" %}<img/>{% endslot %}'
            "{% endcomponent %}"
        )
        t.render(Context({"current_template_path": "/x"}))


class TestSetSlotTag:
    """Tests for {% set_slot %} and {% endset_slot %} tags."""

    def test_set_slot_requires_name(self) -> None:
        """{% set_slot %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="set_slot"):
            Template("{% load components %}{% set_slot %}fallback{% endset_slot %}")

    def test_set_slot_requires_quoted_name(self) -> None:
        """{% set_slot %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template('{% load components %}{% set_slot "" %}x{% endset_slot %}')

    def test_set_slot_renders_fallback_when_slot_empty(self) -> None:
        r"""{% set_slot "x" %}fallback{% endset_slot %} renders fallback when slot not in context."""
        t = Template(
            "{% load components %}"
            '{% set_slot "avatar" %}<span>default</span>{% endset_slot %}'
        )
        result = t.render(Context({}))
        assert "<span>default</span>" in result

    def test_set_slot_renders_slot_content_when_in_context(self) -> None:
        """{% set_slot %} renders slot_xxx from context when present."""
        t = Template(
            "{% load components %}"
            '{% set_slot "avatar" %}<span>default</span>{% endset_slot %}'
        )
        result = t.render(Context({"slot_avatar": '<img src="x"/>'}))
        assert '<img src="x"/>' in result

    def test_set_slot_uses_slash_end_tag(self) -> None:
        """{% set_slot %} can be closed with {% /set_slot %}."""
        t = Template('{% load components %}{% set_slot "x" %}fallback{% /set_slot %}')
        result = t.render(Context({}))
        assert "fallback" in result


class TestModuleCache:
    """ModuleCache LRU and dunder methods."""

    def test_lru_evicts_oldest_when_at_capacity(self, tmp_path: Path) -> None:
        """Adding a new path when full removes the least recently used entry."""
        cache = ModuleCache(maxsize=2)
        p1 = tmp_path / "a.py"
        p2 = tmp_path / "b.py"
        p3 = tmp_path / "c.py"
        m1 = types.ModuleType("a")
        m2 = types.ModuleType("b")
        m3 = types.ModuleType("c")
        cache.set(p1, m1)
        cache.set(p2, m2)
        cache.get(p1)
        cache.set(p3, m3)
        assert p1 in cache
        assert p3 in cache
        assert p2 not in cache

    def test_len_and_contains(self, tmp_path: Path) -> None:
        """__len__ and __contain__ reflect cache keys."""
        cache = ModuleCache()
        p = tmp_path / "x.py"
        assert len(cache) == 0
        assert p not in cache
        cache.set(p, types.ModuleType("x"))
        assert len(cache) == 1
        assert p in cache

    def test_clear_empties_cache(self, tmp_path: Path) -> None:
        """Clear removes all entries and access order."""
        cache = ModuleCache()
        cache.set(tmp_path / "a.py", types.ModuleType("a"))
        cache.clear()
        assert len(cache) == 0


class TestModuleLoader:
    """ModuleLoader disk paths and cache."""

    def test_load_uses_cache_on_second_call(self, tmp_path: Path) -> None:
        """Second load for the same path does not re-read disk (cache hit updates LRU)."""
        path = tmp_path / "mod.py"
        path.write_text("x = 1\n")
        cache = ModuleCache()
        loader = ModuleLoader(cache)
        m1 = loader.load(path)
        m2 = loader.load(path)
        assert m1 is m2

    def test_load_returns_none_when_spec_missing(self, tmp_path: Path) -> None:
        """_load_from_disk returns None when spec_from_file_location returns None."""
        path = tmp_path / "empty.py"
        path.write_text("pass\n")
        with patch(
            "next.components.importlib.util.spec_from_file_location",
            return_value=None,
        ):
            loader = ModuleLoader(ModuleCache())
            assert loader.load(path) is None

    def test_load_returns_none_when_spec_has_no_loader(self, tmp_path: Path) -> None:
        """_load_from_disk returns None when spec.loader is missing."""
        path = tmp_path / "m.py"
        path.write_text("pass\n")
        spec = types.SimpleNamespace(loader=None)
        with patch(
            "next.components.importlib.util.spec_from_file_location",
            return_value=spec,
        ):
            assert ModuleLoader(ModuleCache()).load(path) is None


class TestComponentInfoDunders:
    """ComponentInfo repr, hash, eq, scope_key."""

    def test_repr_contains_fields(self) -> None:
        """Repr includes name and scope fields."""
        root = Path("/app/pages")
        info = ComponentInfo(
            name="card",
            scope_root=root,
            scope_relative="blog",
            template_path=root / "card.djx",
            module_path=None,
            is_simple=True,
        )
        r = repr(info)
        assert "card" in r
        assert "blog" in r
        assert "ComponentInfo" in r

    def test_hash_eq_includes_paths(self) -> None:
        """Same name and scope but different files are not equal. ``scope_key`` can still match."""
        r = Path("/p")
        a = ComponentInfo("x", r, "", Path("/p/a.djx"), None, True)
        b = ComponentInfo("x", r, "", Path("/p/b.djx"), None, True)
        c = ComponentInfo("x", r, "sub", Path("/p/a.djx"), None, True)
        assert a != b
        assert a.scope_key == b.scope_key
        assert a != c
        d = ComponentInfo("x", r, "", Path("/p/a.djx"), None, True)
        assert a == d
        assert hash(a) == hash(d)
        assert a != object()


class TestModuleLoaderDisk:
    """ModuleLoader loads from disk the same way the old helper did."""

    def test_success_and_failure(self, tmp_path: Path) -> None:
        """A valid module loads. Syntax errors yield ``None``."""
        good = tmp_path / "ok.py"
        good.write_text("ANSWER = 42\n")
        loader = ModuleLoader()
        mod = loader.load(good)
        assert mod is not None
        assert mod.ANSWER == 42

        bad = tmp_path / "bad.py"
        bad.write_text("def x(\n")
        assert loader.load(bad) is None

    def test_no_spec_returns_none(self, tmp_path: Path) -> None:
        """Missing import spec yields ``None``."""
        p = tmp_path / "x.py"
        p.write_text("pass\n")
        with patch(
            "next.components.importlib.util.spec_from_file_location",
            return_value=None,
        ):
            assert ModuleLoader(ModuleCache()).load(p) is None


class TestComponentRegistry:
    """ComponentRegistry helpers and dunders."""

    def test_root_mark_clear_iter_contains_len(self, tmp_path: Path) -> None:
        """mark_as_root, is_root, clear, __contains__, __iter__, __len__."""
        reg = ComponentRegistry()
        root = tmp_path.resolve()
        info = ComponentInfo("n", root, "", tmp_path / "n.djx", None, True)
        reg.register(info)
        reg.mark_as_root(root)
        assert reg.is_root(root)
        assert "n" in reg
        assert len(reg) == 1
        assert list(reg) == [info]
        reg.clear()
        assert len(reg) == 0
        assert not reg.is_root(root)

    def test_contains_is_indexed_by_name(self, tmp_path: Path) -> None:
        """Name lookup does not scan every row."""
        reg = ComponentRegistry()
        root = tmp_path.resolve()
        for i in range(50):
            reg.register(
                ComponentInfo(f"c{i}", root, "", tmp_path / f"{i}.djx", None, True),
            )
        assert "c49" in reg
        assert "missing" not in reg


class TestComponentScanner:
    """ComponentScanner edge cases."""

    def test_scan_oserror_on_iterdir(self, tmp_path: Path) -> None:
        """OSError from ``iterdir`` is swallowed. An empty list is returned."""
        err = OSError("no access")

        def boom() -> None:
            raise err

        directory = MagicMock(spec=Path)
        directory.iterdir = boom
        scanner = ComponentScanner()
        assert scanner.scan_directory(directory, tmp_path, "") == []

    def test_composite_py_only_with_component_string(self, tmp_path: Path) -> None:
        """Folder with only component.py exposing component uses py as template path."""
        d = tmp_path / "widget"
        d.mkdir()
        (d / "component.py").write_text('component = "<span>{{ v }}</span>"\n')
        scanner = ComponentScanner()
        found = scanner.scan_directory(tmp_path, tmp_path, "")
        assert len(found) == 1
        w = found[0]
        assert w.name == "widget"
        assert w.template_path == d / "component.py"

    def test_subdir_without_component_files_is_ignored(self, tmp_path: Path) -> None:
        """Directories without component.djx or component.py produce no composite."""
        (tmp_path / "empty_dir").mkdir()
        scanner = ComponentScanner()
        assert scanner.scan_directory(tmp_path, tmp_path, "") == []


class TestComponentExtraRootsFromConfig:
    """``component_extra_roots_from_config`` accepts several ``DIRS`` forms."""

    def test_dirs_tuple_and_path_instances(self, tmp_path: Path) -> None:
        """``DIRS`` accepts tuple and Path elements."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        r1 = component_extra_roots_from_config({"DIRS": (a, b)})
        assert len(r1) == 2
        r2 = component_extra_roots_from_config(
            {"DIRS": [str(b.resolve()), Path(str(a))]},
        )
        assert {Path(p).resolve() for p in r2} == {a.resolve(), b.resolve()}
        missing = tmp_path / "nope"
        assert not missing.exists()
        r3 = component_extra_roots_from_config(
            {"DIRS": [str(a.resolve()), str(missing)]},
        )
        assert r3 == [a.resolve()]

        assert component_extra_roots_from_config({"DIRS": [str(missing)]}) == []


class TestComponentVisibilityResolver:
    """Visibility scoring and path cache."""

    def test_not_visible_when_outside_scope(self, tmp_path: Path) -> None:
        """Template path outside scope_root yields no visible scoped components."""
        pages = tmp_path / "pages"
        about = pages / "about"
        comp_dir = about / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "c.djx").write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo(
                "c",
                pages.resolve(),
                "about",
                comp_dir / "c.djx",
                None,
                True,
            )
        )
        resolver = ComponentVisibilityResolver(reg)
        outside = tmp_path / "elsewhere" / "t.djx"
        outside.parent.mkdir(parents=True)
        assert resolver.resolve_visible(outside) == {}

    def test_path_cache_and_clear_cache(self, tmp_path: Path) -> None:
        """The second resolve reuses the cache. ``clear_cache`` resets it."""
        pages = tmp_path / "pages"
        tmpl = pages / "home.djx"
        tmpl.parent.mkdir(parents=True)
        tmpl.write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo(
                "c",
                pages.resolve(),
                "",
                pages / "_components" / "c.djx",
                None,
                True,
            )
        )
        (pages / "_components").mkdir()
        (pages / "_components" / "c.djx").write_text("y")
        res = ComponentVisibilityResolver(reg)
        r1 = res.resolve_visible(tmpl)
        r2 = res.resolve_visible(tmpl)
        assert r1 == r2
        assert "c" in r1
        assert r1["c"].name == "c"
        res.clear_cache()
        assert res._path_cache == {}

    def test_scope_index_reused_for_second_template_path(self, tmp_path: Path) -> None:
        """Second template path does not rebuild the per-root index."""
        pages = tmp_path / "pages"
        comp_dir = pages / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "c.djx").write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo(
                "c",
                pages.resolve(),
                "about",
                comp_dir / "c.djx",
                None,
                True,
            )
        )
        res = ComponentVisibilityResolver(reg)
        t1 = pages / "about" / "a.djx"
        t2 = pages / "about" / "b.djx"
        t1.parent.mkdir(parents=True, exist_ok=True)
        t1.write_text("z")
        t2.write_text("z")
        res.resolve_visible(t1)
        res.resolve_visible(t2)

    def test_global_root_component_with_scope_relative_not_visible_far_away(
        self, tmp_path: Path
    ) -> None:
        """Marked global root still checks scope path when ``scope_relative`` is set."""
        root = tmp_path / "global"
        root.mkdir()
        reg = ComponentRegistry()
        reg.mark_as_root(root.resolve())
        reg.register(
            ComponentInfo(
                "x",
                root.resolve(),
                "onlyhere",
                root / "x.djx",
                None,
                True,
            )
        )
        res = ComponentVisibilityResolver(reg)
        outsider = tmp_path / "else" / "t.djx"
        outsider.parent.mkdir()
        assert res.resolve_visible(outsider) == {}

    def test_compute_relative_parts_valueerror(self, tmp_path: Path) -> None:
        """Paths on different branches return ``None`` from the helper."""
        reg = ComponentRegistry()
        res = ComponentVisibilityResolver(reg)
        assert (
            res._compute_relative_parts(
                tmp_path / "a" / "t.djx",
                tmp_path / "b",
            )
            is None
        )

    def test_compute_relative_parts_template_at_scope_root(
        self, tmp_path: Path
    ) -> None:
        """Template directory equals scope_root yields a single empty route prefix."""
        reg = ComponentRegistry()
        res = ComponentVisibilityResolver(reg)
        pages = tmp_path / "pages"
        pages.mkdir()
        tmpl = pages / "template.djx"
        tmpl.write_text("x")
        parts = res._compute_relative_parts(tmpl.resolve(), pages.resolve())
        assert parts == [""]


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
        ctx: dict[str, object] = {}
        next_components_mod._merge_csrf_context(ctx, None)
        assert "csrf_token" not in ctx

    def test_merge_csrf_context_skips_when_csrf_token_present(self) -> None:
        """Do not replace an existing ``csrf_token`` (caller supplied)."""
        req = RequestFactory().get("/")
        existing = "__test_merge_csrf_existing__"
        ctx: dict[str, object] = {"csrf_token": existing}
        next_components_mod._merge_csrf_context(ctx, req)
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
        (tmp_path / "s.djx").write_text("<b>{% csrf_token %}</b>")
        info = ComponentInfo("s", tmp_path, "", tmp_path / "s.djx", None, True)
        tl = ComponentTemplateLoader(ModuleLoader())
        sr = SimpleComponentRenderer(tl)
        req = RequestFactory().get("/")
        html = sr.render(info, {}, request=req)
        assert "csrfmiddlewaretoken" in html
        assert "<b>" in html


class TestComponentsFactoryManager:
    """ComponentsFactory import path and ComponentsManager branches."""

    def test_create_backend_imports_class_and_passes_config(self) -> None:
        """Backend is loaded by dotted path and receives the full config dict."""
        b = ComponentsFactory.create_backend(
            {
                "BACKEND": "next.components.DummyBackend",
                "OPTIONS": {"marker": 7},
            },
        )
        assert isinstance(b, DummyBackend)
        assert b.config["OPTIONS"]["marker"] == 7

    def test_dummy_backend_lookups_are_empty(self) -> None:
        """DummyBackend does not resolve names and reports no visible components."""
        b = DummyBackend({})
        assert b.get_component("x", Path("/t.djx")) is None
        assert b.collect_visible_components(Path("/t.djx")) == {}

    def test_manager_skips_non_list_config_and_non_dict_entries(self) -> None:
        """If ``DEFAULT_COMPONENT_BACKENDS`` is not a list, return early. Non-dict entries are skipped."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list("bad")
        with patch("next.components.next_framework_settings", mock_ns):
            mgr._reload_config()
            assert mgr._backends == []

        mgr2 = ComponentsManager()
        mock_ns2 = _next_framework_settings_component_backends_list(
            [
                None,
                {
                    "BACKEND": "next.components.FileComponentsBackend",
                    "DIRS": [],
                    "COMPONENTS_DIR": "_components",
                },
            ],
        )
        with patch("next.components.next_framework_settings", mock_ns2):
            mgr2._reload_config()
            assert len(mgr2._backends) >= 1

    def test_manager_swallows_backend_init_exception(self) -> None:
        """An exception from ``create_backend`` is logged. The backend is not appended."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [
                {
                    "BACKEND": "next.components.BoomBackend",
                    "DIRS": [],
                    "COMPONENTS_DIR": "_components",
                },
            ],
        )
        with patch("next.components.next_framework_settings", mock_ns):
            mgr._reload_config()
        assert mgr._backends == []

    def test_manager_collect_visible_first_backend_wins(self) -> None:
        """Same component name from two backends: first backend wins."""
        mgr = ComponentsManager()
        info1 = ComponentInfo("a", Path("/"), "", None, None, True)
        info2 = ComponentInfo("a", Path("/b"), "", None, None, True)
        b1 = MagicMock()
        b1.collect_visible_components.return_value = {"a": info1}
        b2 = MagicMock()
        b2.collect_visible_components.return_value = {"a": info2}
        mgr._backends = [b1, b2]
        merged = mgr.collect_visible_components(Path("/t.djx"))
        assert merged["a"] is info1

    def test_manager_get_component_none_from_all_backends(self) -> None:
        """get_component returns None when every backend returns None."""
        mgr = ComponentsManager()
        b = MagicMock()
        b.get_component.return_value = None
        mgr._backends = [b]
        assert mgr.get_component("x", Path("/p")) is None

    def test_manager_get_component_returns_first_hit(self) -> None:
        """get_component returns first non-None from backends."""
        mgr = ComponentsManager()
        hit = ComponentInfo("n", Path("/"), "", None, None, True)
        b1 = MagicMock()
        b1.get_component.return_value = None
        b2 = MagicMock()
        b2.get_component.return_value = hit
        mgr._backends = [b1, b2]
        assert mgr.get_component("n", Path("/t")) is hit


class TestInjectComponentContext:
    """_inject_component_context early exits."""

    def test_no_op_when_no_module_path(self) -> None:
        """When module_path is None, nothing is merged."""
        info = ComponentInfo("s", Path("/"), "", Path("/t.djx"), None, True)
        data: dict[str, object] = {"keep": 1}
        _inject_component_context(info, data, None)
        assert data == {"keep": 1}


class TestComponentContextRegistryInternals:
    """Duplicate unkeyed, _is_same_function edge cases, __len__."""

    def test_duplicate_unkeyed_raises(self, tmp_path: Path) -> None:
        """Second different unkeyed registration raises with unkeyed message."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "comp" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def first() -> dict[str, int]:
            return {"a": 1}

        def second() -> dict[str, int]:
            return {"b": 2}

        reg.register(p, None, first)
        with pytest.raises(ValueError, match="unkeyed"):
            reg.register(p, None, second)

    def test_len_counts_all_keys(self, tmp_path: Path) -> None:
        """__len__ sums registrations per component path."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "c" / "component.py").resolve()
        p.parent.mkdir(parents=True)
        assert len(reg) == 0

        def fx() -> int:
            return 1

        def fy() -> int:
            return 2

        reg.register(p, "x", fx)
        reg.register(p, "y", fy)
        assert len(reg) == 2

    def test_duplicate_after_getsourcefile_oserror(self, tmp_path: Path) -> None:
        """When inspect.getsourcefile fails, different functions are not 'same'."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "d" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def f1() -> int:
            return 1

        def f2() -> int:
            return 2

        reg.register(p, "slot", f1)
        nope = OSError("nope")
        with (
            patch.object(inspect, "getsourcefile", side_effect=nope),
            pytest.raises(ValueError, match="Duplicate"),
        ):
            reg.register(p, "slot", f2)

    def test_is_same_function_false_when_sourcefile_missing(
        self, tmp_path: Path
    ) -> None:
        """Same __name__ but getsourcefile returns None leads to duplicate error."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "e" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def g1() -> int:
            return 1

        def g2() -> int:
            return 2

        g1.__name__ = "g"
        g2.__name__ = "g"

        reg.register(p, "slot", g1)

        def gs(_: object) -> str | None:
            return None

        with (
            patch.object(inspect, "getsourcefile", gs),
            pytest.raises(ValueError, match="Duplicate"),
        ):
            reg.register(p, "slot", g2)

    def test_is_same_function_true_same_file_same_name(self, tmp_path: Path) -> None:
        """Heuristic: identical name and source file counts as same function."""

        def h() -> int:
            return 7

        reg = ComponentContextRegistry()
        p = (tmp_path / "f" / "component.py").resolve()
        p.parent.mkdir(parents=True)
        reg.register(p, "x", h)
        reg.register(p, "x", h)

    def test_is_same_function_path_compare_raises_typeerror(
        self, tmp_path: Path
    ) -> None:
        """If Path.resolve raises, _is_same_function returns False (except branch)."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "g" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def u1() -> int:
            return 1

        def u2() -> int:
            return 2

        u1.__name__ = "u"
        u2.__name__ = "u"
        reg.register(p, "slot", u1)

        def gs(fn: object) -> object:
            return str(p) if fn is u1 else 123

        with (
            patch.object(inspect, "getsourcefile", gs),
            pytest.raises(ValueError, match="Duplicate"),
        ):
            reg.register(p, "slot", u2)


class TestComponentContextManagerFrames:
    """How ComponentContextManager finds the caller's file."""

    def test_get_caller_path_raises_when_back_count_too_large(self) -> None:
        """Exceeding frame chain raises RuntimeError."""
        mgr = ComponentContextManager()
        with pytest.raises(RuntimeError, match="Could not determine caller"):
            mgr._get_caller_path(10_000)

    def test_get_caller_path_raises_when_no_python_file_in_chain(self) -> None:
        """Walk stops if no frame exposes a ``.py`` __file__."""
        inner = types.SimpleNamespace(f_back=None, f_globals={"__file__": "/x.txt"})
        start = types.SimpleNamespace(
            f_back=inner,
            f_globals={"__file__": "/y.txt"},
        )
        mgr = ComponentContextManager()
        with (
            patch("next.components.inspect.currentframe", return_value=start),
            pytest.raises(RuntimeError, match="no __file__ in caller frames"),
        ):
            mgr._get_caller_path(1)

    def test_context_decorator_without_key_registers_caller(
        self, tmp_path: Path
    ) -> None:
        """@mgr.context on a function registers unkeyed context at caller file."""
        script = tmp_path / "comp" / "component.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            textwrap.dedent(
                """
                from next.components import ComponentContextManager
                mgr = ComponentContextManager()

                @mgr.context
                def ctx():
                    return {"v": 1}
                """
            ).lstrip()
        )
        spec = importlib.util.spec_from_file_location("dyn_comp_ctx", script)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mgr = mod.mgr
        funcs = mgr.get_functions(script.resolve())
        assert len(funcs) == 1
        assert funcs[0].key is None

    def test_context_decorator_with_string_key_registers(self, tmp_path: Path) -> None:
        """@mgr.context('key') uses _get_caller_path(1) and keyed register branch."""
        script = tmp_path / "keyed" / "component.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            textwrap.dedent(
                """
                from next.components import ComponentContextManager
                mgr = ComponentContextManager()

                @mgr.context("slot")
                def get_slot():
                    return 99
                """
            ).lstrip()
        )
        spec = importlib.util.spec_from_file_location("dyn_keyed_ctx", script)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mgr = mod.mgr
        funcs = mgr.get_functions(script.resolve())
        assert len(funcs) == 1
        assert funcs[0].key == "slot"


class TestGetComponentPathsForWatch:
    """``get_component_paths_for_watch`` mirrors discovery without mutating managers."""

    def test_empty_when_backend_settings_not_lists(self) -> None:
        """Return empty sets when ``DEFAULT_*_BACKENDS`` are not lists."""
        mock_nf = SimpleNamespace(
            DEFAULT_PAGE_BACKENDS="not-a-list",
            DEFAULT_COMPONENT_BACKENDS="not-a-list",
        )
        with patch("next.components.next_framework_settings", mock_nf):
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
                        "COMPONENTS_DIR": "_components",
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
                        "COMPONENTS_DIR": "_components",
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [],
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
                        "COMPONENTS_DIR": "_components",
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
                        "COMPONENTS_DIR": "_components",
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [],
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
                        "COMPONENTS_DIR": "_components",
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [],
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
                "next.components.ComponentsFactory.create_backend",
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
                        "COMPONENTS_DIR": "_components",
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [],
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
        """Glob can match a file named like ``COMPONENTS_DIR``; it is ignored."""
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
                        "COMPONENTS_DIR": "_components",
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [],
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
