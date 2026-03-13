from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.template.base import TemplateSyntaxError

from next.checks import (
    check_component_py_no_pages_context,
    check_duplicate_component_names,
)
from next.components import (
    ComponentInfo,
    ComponentsFactory,
    ComponentsManager,
    FileComponentsBackend,
    component,
    components_manager,
    get_component,
    load_component_template,
    render_component,
)


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
        """With app_dirs=False and no root dirs, no components are visible."""
        backend = FileComponentsBackend(app_dirs=False, options={})
        visible = backend.collect_visible_components(Path("/tmp/some/template.djx"))
        assert visible == {}

    def test_get_component_returns_none_when_empty(self) -> None:
        """get_component returns None when no backends have it."""
        backend = FileComponentsBackend(app_dirs=False, options={})
        assert backend.get_component("card", Path("/tmp/template.djx")) is None

    def test_discover_in_component_root_simple(self, tmp_path: Path) -> None:
        """Root component dir: .djx files are discovered as simple components."""
        (tmp_path / "header.djx").write_text("<header>Hi</header>")
        backend = FileComponentsBackend(app_dirs=False, options={})
        entries = backend._discover_in_component_root(tmp_path)
        assert len(entries) == 1
        _scope_root, scope_rel, name, info = entries[0]
        assert name == "header"
        assert scope_rel == ""
        assert info.is_simple
        assert info.template_path == tmp_path / "header.djx"

    def test_discover_in_component_root_composite(self, tmp_path: Path) -> None:
        """Root component dir: subdir with component.djx is composite."""
        (tmp_path / "profile").mkdir()
        (tmp_path / "profile" / "component.djx").write_text("<div>profile</div>")
        backend = FileComponentsBackend(app_dirs=False, options={})
        entries = backend._discover_in_component_root(tmp_path)
        assert len(entries) == 1
        _scope_root, _scope_rel, name, info = entries[0]
        assert name == "profile"
        assert not info.is_simple
        assert info.template_path == tmp_path / "profile" / "component.djx"

    def test_discover_in_pages_root_finds_components_dir(self, tmp_path: Path) -> None:
        """_discover_in_pages_root finds _components and scans it."""
        (tmp_path / "_components").mkdir()
        (tmp_path / "_components" / "card.djx").write_text("<div>card</div>")
        backend = FileComponentsBackend(
            components_dir="_components", app_dirs=False, options={}
        )
        entries = backend._discover_in_pages_root(tmp_path)
        assert len(entries) == 1
        assert entries[0][2] == "card"

    def test_get_root_component_roots_from_options(self, tmp_path: Path) -> None:
        """_get_root_component_roots returns paths from COMPONENTS_DIRS or COMPONENTS_DIR."""
        backend = FileComponentsBackend(
            app_dirs=False,
            options={"COMPONENTS_DIRS": ["/nonexistent/root"]},
        )
        roots = backend._get_root_component_roots()
        assert roots == []
        backend2 = FileComponentsBackend(
            app_dirs=False,
            options={"COMPONENTS_DIR": str(tmp_path)},
        )
        roots2 = backend2._get_root_component_roots()
        assert len(roots2) == 1
        assert roots2[0] == tmp_path.resolve()

    def test_discover_all_via_patched_app_roots(self, tmp_path: Path) -> None:
        """_discover_all runs when _get_app_pages_roots returns a path with _components."""
        (tmp_path / "_components").mkdir()
        (tmp_path / "_components" / "card.djx").write_text("<div>card</div>")
        backend = FileComponentsBackend(
            components_dir="_components",
            app_dirs=True,
            options={},
        )
        with (
            patch.object(backend, "_get_app_pages_roots", return_value=[tmp_path]),
            patch.object(backend, "_get_root_component_roots", return_value=[]),
        ):
            backend._ensure_loaded()
        assert len(backend._registry) == 1
        assert backend._registry[0][2] == "card"

    def test_root_components_visible_from_any_path(self, tmp_path: Path) -> None:
        """Root component roots are visible from any template path."""
        (tmp_path / "global.djx").write_text("<div>global</div>")
        backend = FileComponentsBackend(app_dirs=False, options={})
        backend._registry = [
            (
                tmp_path,
                "",
                "global",
                ComponentInfo(
                    name="global",
                    scope_root=tmp_path,
                    scope_relative="",
                    template_path=tmp_path / "global.djx",
                    module_path=None,
                    is_simple=True,
                ),
            ),
        ]
        backend._root_roots.add(tmp_path)
        backend._loaded = True
        visible = backend.collect_visible_components(Path("/other/path/template.djx"))
        assert "global" in visible

    def test_visible_from_template_under_scope(self, tmp_path: Path) -> None:
        """Component in scope_relative is visible from template under that path."""
        comp_dir = tmp_path / "pages" / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "card.djx").write_text("<div>card</div>")
        backend = FileComponentsBackend(app_dirs=False, options={})
        backend._registry = [
            (
                tmp_path / "pages",
                "about",
                "card",
                ComponentInfo(
                    name="card",
                    scope_root=tmp_path / "pages",
                    scope_relative="about",
                    template_path=comp_dir / "card.djx",
                    module_path=None,
                    is_simple=True,
                ),
            ),
        ]
        backend._loaded = True
        template_path = tmp_path / "pages" / "about" / "team" / "template.djx"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        visible = backend.collect_visible_components(template_path)
        assert "card" in visible
        assert visible["card"].name == "card"


class TestComponentsFactory:
    """Tests for ComponentsFactory."""

    def test_create_backend_file_default(self) -> None:
        """Create FileComponentsBackend with default config."""
        config = {"BACKEND": "next.components.FileComponentsBackend"}
        backend = ComponentsFactory.create_backend(config)
        assert isinstance(backend, FileComponentsBackend)
        assert backend.components_dir == "_components"
        assert backend.app_dirs is True

    def test_create_backend_file_with_options(self) -> None:
        """Create FileComponentsBackend with OPTIONS."""
        config = {
            "BACKEND": "next.components.FileComponentsBackend",
            "APP_DIRS": False,
            "OPTIONS": {"COMPONENTS_DIR": "components", "PAGES_DIR": "views"},
        }
        backend = ComponentsFactory.create_backend(config)
        assert isinstance(backend, FileComponentsBackend)
        assert backend.components_dir == "components"
        assert backend.app_dirs is False
        assert backend.options.get("PAGES_DIR") == "views"

    def test_create_backend_unknown_raises(self) -> None:
        """Unknown backend name raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported backend"):
            ComponentsFactory.create_backend(
                {"BACKEND": "next.components.UnknownBackend"}
            )


class TestComponentsManager:
    """Tests for ComponentsManager."""

    def test_get_component_empty_when_no_config(self) -> None:
        """When NEXT_COMPONENTS is not set or empty, get_component returns None."""
        with patch("next.components.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = []
            manager = ComponentsManager()
            manager._reload_config()
            assert manager.get_component("card", Path("/tmp/t.djx")) is None

    def test_collect_visible_components_merges_backends(self) -> None:
        """collect_visible_components merges from all backends, first wins."""
        with patch("next.components.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = []
            manager = ComponentsManager()
            manager._reload_config()
            assert manager.collect_visible_components(Path("/x")) == {}

    def test_reload_config_swallows_backend_creation_error(self) -> None:
        """When create_backend raises, _reload_config logs and continues."""
        with patch("next.components.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = [
                {"BACKEND": "next.components.NonexistentBackend", "OPTIONS": {}},
            ]
            manager = ComponentsManager()
            manager._reload_config()
            assert len(manager._backends) == 0


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
        """check_duplicate_component_names returns [] when NEXT_COMPONENTS not set."""
        with patch("next.checks.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = None
            assert check_duplicate_component_names() == []

    def test_check_component_py_no_pages_context_empty_when_no_config(self) -> None:
        """check_component_py_no_pages_context returns [] when NEXT_COMPONENTS not set."""
        with patch("next.checks.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = None
            assert check_component_py_no_pages_context() == []

    def test_check_duplicate_component_names_reports_duplicate(
        self, tmp_path: Path
    ) -> None:
        """check_duplicate_component_names reports when same name in same scope."""
        (tmp_path / "a.djx").write_text("a")
        (tmp_path / "b.djx").write_text("b")
        fake_backend = FileComponentsBackend(app_dirs=False, options={})
        fake_backend._registry = [
            (
                tmp_path,
                "",
                "card",
                ComponentInfo("card", tmp_path, "", tmp_path / "a.djx", None, True),
            ),
            (
                tmp_path,
                "",
                "card",
                ComponentInfo("card", tmp_path, "", tmp_path / "b.djx", None, True),
            ),
        ]
        fake_backend._loaded = True
        with patch("next.checks.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = [{"BACKEND": "x", "OPTIONS": {}}]
            with patch("next.checks.ComponentsManager") as mock_manager_klass:
                mock_manager = mock_manager_klass.return_value
                mock_manager._reload_config = lambda: None
                mock_manager._backends = [fake_backend]
                errors = check_duplicate_component_names()
        assert any(e.id == "next.E020" for e in errors)

    def test_check_component_py_no_pages_context_reports_import(
        self, tmp_path: Path
    ) -> None:
        """check_component_py_no_pages_context reports when component.py imports context from next.pages."""
        (tmp_path / "component.py").write_text("from next.pages import context\n")
        fake_backend = FileComponentsBackend(app_dirs=False, options={})
        fake_backend._registry = [
            (
                tmp_path,
                "",
                "bad",
                ComponentInfo(
                    "bad",
                    tmp_path,
                    "",
                    None,
                    tmp_path / "component.py",
                    False,
                ),
            ),
        ]
        fake_backend._loaded = True
        with patch("next.checks.settings") as mock_settings:
            mock_settings.NEXT_COMPONENTS = [{"BACKEND": "x", "OPTIONS": {}}]
            with patch("next.checks.ComponentsManager") as mock_manager_klass:
                mock_manager = mock_manager_klass.return_value
                mock_manager._reload_config = lambda: None
                mock_manager._backends = [fake_backend]
                errors = check_component_py_no_pages_context()
        assert any(e.id == "next.E021" for e in errors)


class TestComponentContextManager:
    """Tests for ComponentContextManager."""

    def test_component_context_injected_on_render(self, tmp_path: Path) -> None:
        """When component has context registered, _inject_component_context adds it to render."""
        (tmp_path / "comp").mkdir()
        (tmp_path / "comp" / "component.djx").write_text("<span>{{ injected }}</span>")
        (tmp_path / "comp" / "component.py").write_text("# empty\n")
        component.register(
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
        """Keyed context (component.context('key')) is injected as context_data[key]."""
        (tmp_path / "k").mkdir()
        (tmp_path / "k" / "component.djx").write_text("<span>{{ count }}</span>")
        (tmp_path / "k" / "component.py").write_text("# empty\n")
        component.register(
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

        component.register(path, "data", my_context)
        registry = component.get_registry_for_path(path)
        assert "data" in registry
        func, _ = registry["data"]
        assert func(None) == {"count": 1}


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
