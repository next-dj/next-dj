from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import HttpRequest

from next.checks import _load_python_module
from next.pages import Page, context, page
from next.pages.loaders import (
    LayoutManager,
    LayoutTemplateLoader,
    TemplateLoader,
)
from next.pages.registry import PageContextRegistry


class TestPage:
    """Test cases for Page class."""

    def test_init(self, page_instance) -> None:
        """Test Page initialization."""
        assert page_instance._template_registry == {}
        assert isinstance(page_instance._context_manager, PageContextRegistry)
        assert isinstance(page_instance._layout_manager, LayoutManager)

    def test_register_template_direct(self, page_instance) -> None:
        """Test register_template method with direct file path."""
        file_path = Path("/test/path/page.py")
        template_str = "Hello {{ name }}!"

        page_instance.register_template(file_path, template_str)

        assert file_path in page_instance._template_registry
        assert page_instance._template_registry[file_path] == template_str

    @pytest.mark.parametrize(
        ("decorator_type", "expected_key", "frame_chain"),
        [
            ("with_key", "user_name", "f_back"),
            ("without_key", None, "f_back.f_back"),
            ("without_parentheses", None, "f_back.f_back"),
        ],
        ids=["with_key", "without_key", "without_parentheses"],
    )
    def test_context_decorator_variations(
        self,
        page_instance,
        context_temp_file,
        mock_frame,
        decorator_type,
        expected_key,
        frame_chain,
    ) -> None:
        """Test context decorator with different variations."""
        # set up the frame chain based on the test case
        frame = mock_frame.return_value
        for attr in frame_chain.split("."):
            frame = getattr(frame, attr)
        frame.f_globals = {"__file__": str(context_temp_file)}

        if decorator_type == "with_key":

            @page_instance.context("user_name")
            def get_user_name() -> str:
                return "John Doe"

            func = get_user_name
        else:

            @page_instance.context
            def get_context_data():
                return {"key1": "value1", "key2": "value2"}

            func = get_context_data

        # verify context function was registered
        assert context_temp_file in page_instance._context_manager._context_registry
        assert (
            expected_key
            in page_instance._context_manager._context_registry[context_temp_file]
        )
        func_registered, inherit, serialize = (
            page_instance._context_manager._context_registry[context_temp_file][
                expected_key
            ]
        )
        assert func_registered == func
        assert inherit is False
        assert serialize is False

    def test_context_decorator_with_inherit_context(
        self,
        page_instance,
        context_temp_file,
        mock_frame,
    ) -> None:
        """Test context decorator with inherit_context=True."""
        mock_frame.return_value.f_back.f_globals = {"__file__": str(context_temp_file)}

        @page_instance.context("inherited_key", inherit_context=True)
        def get_inherited_value() -> str:
            return "inherited_value"

        # verify context function was registered with inherit_context=True
        assert context_temp_file in page_instance._context_manager._context_registry
        assert (
            "inherited_key"
            in page_instance._context_manager._context_registry[context_temp_file]
        )
        func, inherit, serialize = page_instance._context_manager._context_registry[
            context_temp_file
        ]["inherited_key"]
        assert func == get_inherited_value
        assert inherit is True
        assert serialize is False

    def test_context_decorator_without_key_inherit_context(
        self,
        page_instance,
        context_temp_file,
        mock_frame,
    ) -> None:
        """Test context decorator without key but with inherit_context=True."""
        mock_frame.return_value.f_back.f_back.f_globals = {
            "__file__": str(context_temp_file),
        }

        @page_instance.context(inherit_context=True)
        def get_context_data():
            return {"key1": "value1", "key2": "value2"}

        # verify context function was registered with inherit_context=True
        assert context_temp_file in page_instance._context_manager._context_registry
        assert (
            None in page_instance._context_manager._context_registry[context_temp_file]
        )
        func, inherit, serialize = page_instance._context_manager._context_registry[
            context_temp_file
        ][None]
        assert func == get_context_data
        assert inherit is True
        assert serialize is False

    @pytest.mark.parametrize(
        ("test_case", "template_str", "context_setup", "render_kwargs", "expected"),
        [
            (
                "template_only",
                "Hello {{ name }}!",
                {},
                {"name": "World"},
                "Hello World!",
            ),
            (
                "context_with_keys",
                "Hello {{ user_name }}! You have {{ item_count }} items.",
                {"user_name": lambda: "Alice", "item_count": lambda: 5},
                {},
                "Hello Alice! You have 5 items.",
            ),
            (
                "context_without_keys",
                "Hello {{ name }}! Status: {{ status }}",
                {None: lambda: {"name": "Bob", "status": "active"}},
                {},
                "Hello Bob! Status: active",
            ),
            (
                "mixed_context",
                "Hello {{ name }}! Role: {{ role }}. Items: {{ count }}",
                {
                    None: lambda: {"name": "Charlie", "role": "admin"},
                    "count": lambda: 10,
                },
                {},
                "Hello Charlie! Role: admin. Items: 10",
            ),
            (
                "template_override",
                "Hello {{ name }}! Count: {{ count }}",
                {None: lambda *args, **kwargs: {"name": "ContextName", "count": 5}},
                {"name": "OverrideName", "count": 20},
                "Hello ContextName! Count: 5",
            ),
            ("no_context", "Hello {{ name }}!", {}, {"name": "Test"}, "Hello Test!"),
            ("empty_context", "Static content", {}, {}, "Static content"),
        ],
        ids=[
            "template_only",
            "context_with_keys",
            "context_without_keys",
            "mixed_context",
            "template_override",
            "no_context",
            "empty_context",
        ],
    )
    def test_render_scenarios(
        self,
        page_instance,
        test_file_path,
        test_case,
        template_str,
        context_setup,
        render_kwargs,
        expected,
    ) -> None:
        """Test various render scenarios with parametrized test cases."""
        # register template
        page_instance.register_template(test_file_path, template_str)

        # register context functions if any
        if context_setup:
            for key, func in context_setup.items():
                page_instance._context_manager.register_context(
                    test_file_path,
                    key,
                    func,
                )

        # render
        result = page_instance.render(test_file_path, **render_kwargs)

        assert result == expected

    def test_render_with_multiple_files(self, page_instance) -> None:
        """Test render method with multiple files having different templates and contexts."""
        # first file
        file1 = Path("/test/path/page1.py")
        template1 = "Page 1: {{ title }}"
        page_instance.register_template(file1, template1)
        page_instance._context_manager.register_context(
            file1,
            "title",
            lambda: "First Page",
        )

        # second file
        file2 = Path("/test/path/page2.py")
        template2 = "Page 2: {{ title }}"
        page_instance.register_template(file2, template2)
        page_instance._context_manager.register_context(
            file2,
            "title",
            lambda: "Second Page",
        )

        # render both
        result1 = page_instance.render(file1)
        result2 = page_instance.render(file2)

        assert result1 == "Page 1: First Page"
        assert result2 == "Page 2: Second Page"

    def test_render_with_inherited_context(self, page_instance, tmp_path) -> None:
        """Test render method with inherited context from layout directories."""
        # create layout structure
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        # create child directory
        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        # register template for child page
        template_str = "Child page: {{ inherited_var }}"
        page_instance.register_template(child_page_file, template_str)

        # register context in layout page.py with inherit_context=True
        def layout_func() -> str:
            return "inherited_value"

        page_instance._context_manager.register_context(
            page_file,
            "inherited_var",
            layout_func,
            inherit_context=True,
        )

        # render child page
        result = page_instance.render(child_page_file)

        assert "Child page: inherited_value" in result

    def test_render_with_inherited_context_override(
        self, page_instance, tmp_path
    ) -> None:
        """Test that child page context overrides inherited context."""
        # create layout structure
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        # create child directory
        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        # register template for child page
        template_str = "Child page: {{ var }}"
        page_instance.register_template(child_page_file, template_str)

        # register context in layout page.py with inherit_context=True
        def layout_func() -> str:
            return "layout_value"

        page_instance._context_manager.register_context(
            page_file,
            "var",
            layout_func,
            inherit_context=True,
        )

        # register context in child page.py (should override inherited)
        def child_func() -> str:
            return "child_value"

        page_instance._context_manager.register_context(
            child_page_file,
            "var",
            child_func,
            inherit_context=False,
        )

        # render child page
        result = page_instance.render(child_page_file)

        # child context should override inherited context
        assert "Child page: child_value" in result

    def test_context_registry_defaultdict_behavior(
        self, page_instance, test_file_path
    ) -> None:
        """Test that context registry uses defaultdict-like behavior."""
        # register context function - should create the file entry
        page_instance._context_manager.register_context(
            test_file_path,
            "test_key",
            lambda: "test_value",
        )

        assert test_file_path in page_instance._context_manager._context_registry
        assert (
            "test_key"
            in page_instance._context_manager._context_registry[test_file_path]
        )


class TestPageHasTemplateAndLazyRender:
    """Tests for Page.has_template and lazy template loading in render()."""

    def test_has_template_true_for_djx(self, page_instance, tmp_path) -> None:
        """has_template returns True when template.djx exists."""
        (tmp_path / "template.djx").write_text("<h1>Hi</h1>")
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        assert page_instance.has_template(page_file, module=None) is True

    def test_has_template_true_for_module_with_template_attr(
        self, page_instance, tmp_path
    ) -> None:
        """has_template returns True when module has template attribute."""
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "<p>{{ x }}</p>"')
        module = _load_python_module(page_file)
        assert module is not None
        assert page_instance.has_template(page_file, module) is True

    def test_has_template_false_when_no_template(self, page_instance, tmp_path) -> None:
        """has_template returns False when no template.djx and no template attr."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        module = _load_python_module(page_file)
        assert page_instance.has_template(page_file, module) is False
        assert page_instance.has_template(page_file, module=None) is False

    def test_render_loads_template_when_not_in_registry(
        self, page_instance, tmp_path
    ) -> None:
        """render() calls _load_template_for_file when file_path not in registry."""
        page_file = tmp_path / "page.py"
        page_file.write_text("y = 2")
        (tmp_path / "template.djx").write_text("<h1>{{ title }}</h1>")
        assert page_file not in page_instance._template_registry
        result = page_instance.render(page_file, title="Lazy")
        assert page_file in page_instance._template_registry
        assert "Lazy" in result

    def test_render_with_no_body_source_returns_empty_block(
        self, page_instance, tmp_path
    ) -> None:
        """Page.render returns an empty `{% block template %}` slot when no source exists."""
        page_file = tmp_path / "page.py"
        page_file.write_text("y = 1")
        result = page_instance.render(page_file)
        assert result == ""

    def test_render_invalidates_cache_when_template_stale(
        self, page_instance, tmp_path
    ) -> None:
        """When source .djx mtime changes, render() reloads template."""
        page_file = tmp_path / "page.py"
        page_file.write_text("z = 3")
        djx = tmp_path / "template.djx"
        djx.write_text("<h1>{{ title }}</h1>")
        result1 = page_instance.render(page_file, title="First")
        assert "First" in result1
        djx.write_text("<h2>{{ title }}</h2>")
        result2 = page_instance.render(page_file, title="Second")
        assert "<h2>Second</h2>" in result2

    def test_render_injects_current_template_path_in_context(
        self, page_instance, tmp_path
    ) -> None:
        """render() adds current_template_path to template context for component resolution."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("path={{ current_template_path }}")
        result = page_instance.render(page_file)
        assert "current_template_path" in result or str(tmp_path) in result
        assert "path=" in result

    def test_record_template_source_mtimes_empty_paths(
        self, page_instance, tmp_path
    ) -> None:
        """_record_template_source_mtimes returns early when no source paths."""
        page_file = tmp_path / "page.py"
        page_instance._record_template_source_mtimes(page_file)
        assert page_file not in page_instance._template_source_mtimes

    def test_is_template_stale_handles_oserror(self, page_instance, tmp_path) -> None:
        """_is_template_stale catches OSError when stat() fails (e.g. file removed)."""
        page_file = tmp_path / "page.py"
        missing_path = tmp_path / "removed.djx"
        page_instance._template_source_mtimes[page_file] = {missing_path: 1000.0}
        assert page_instance._is_template_stale(page_file) is False


class TestGlobalPageInstance:
    """Test cases for global page instance."""

    @pytest.fixture(autouse=True)
    def clear_global_state(self):
        """Clear global page state before each test."""
        page._template_registry.clear()
        page._context_manager._context_registry.clear()
        yield
        page._template_registry.clear()
        page._context_manager._context_registry.clear()

    def test_global_page_instance(self) -> None:
        """Test that global page instance is properly initialized."""
        assert page is not None
        assert isinstance(page, Page)
        assert page._template_registry == {}
        assert page._context_manager._context_registry == {}

    def test_context_alias(self) -> None:
        """Test that context alias points to page.context."""
        assert context == page.context

    def test_global_page_template_registration(self, global_file_path) -> None:
        """Test template registration using global page instance."""
        template_str = "Global template: {{ message }}"
        page.register_template(global_file_path, template_str)

        assert global_file_path in page._template_registry
        assert page._template_registry[global_file_path] == template_str

    def test_global_page_context_registration(
        self, global_file_path, mock_frame
    ) -> None:
        """Test context registration using global page instance."""
        mock_frame.return_value.f_back.f_globals = {"__file__": str(global_file_path)}

        @page.context("global_key")
        def get_global_value() -> str:
            return "global_value"

        assert global_file_path in page._context_manager._context_registry
        assert "global_key" in page._context_manager._context_registry[global_file_path]

    def test_global_page_render(self, global_file_path, mock_frame) -> None:
        """Test rendering using global page instance."""
        template_str = "Global: {{ key }}"
        page.register_template(global_file_path, template_str)

        mock_frame.return_value.f_back.f_globals = {"__file__": str(global_file_path)}

        @page.context("key")
        def get_key() -> str:
            return "value"

        result = page.render(global_file_path)
        assert result == "Global: value"

    def test_context_decorator_with_global_page(
        self, global_file_path, mock_frame
    ) -> None:
        """Test context decorator with global page instance."""
        mock_frame.return_value.f_back.f_globals = {"__file__": str(global_file_path)}

        @context("test_key")
        def test_function() -> str:
            return "test_value"

        assert global_file_path in page._context_manager._context_registry
        assert "test_key" in page._context_manager._context_registry[global_file_path]
        func, inherit, serialize = page._context_manager._context_registry[
            global_file_path
        ]["test_key"]
        assert func == test_function
        assert inherit is False
        assert serialize is False

    @pytest.mark.parametrize(
        ("test_case", "frame_setup"),
        [
            (
                "frame_none",
                lambda mock_frame: setattr(mock_frame.return_value, "f_back", None),
            ),
            (
                "final_none",
                lambda mock_frame: setattr(mock_frame, "return_value", None),
            ),
            (
                "no_file",
                lambda mock_frame: setattr(
                    mock_frame.return_value.f_back,
                    "f_globals",
                    {"__file__": None},
                ),
            ),
            (
                "exhausted_frames",
                lambda mock_frame: setattr(
                    mock_frame.return_value.f_back,
                    "f_back",
                    None,
                ),
            ),
        ],
        ids=["frame_none", "final_none", "no_file", "exhausted_frames"],
    )
    def test_get_caller_path_error_cases(
        self, page_instance, test_case, frame_setup
    ) -> None:
        """Test _get_caller_path error cases with parametrized test cases."""
        with patch("next.pages.manager.inspect.currentframe") as mock_frame:
            frame_setup(mock_frame)

            with pytest.raises(
                RuntimeError,
                match="Could not determine caller file path",
            ):
                page_instance._get_caller_path(1)


class TestLayoutManager:
    """Test cases for LayoutManager."""

    def test_init(self) -> None:
        """Test LayoutManager initialization."""
        manager = LayoutManager()
        assert manager._layout_registry == {}
        assert isinstance(manager._layout_loader, LayoutTemplateLoader)

    def test_discover_layouts_for_template(self, tmp_path) -> None:
        """Test discover_layouts_for_template method."""
        manager = LayoutManager()

        # create layout structure
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>Test</h1>")

        page_file = sub_dir / "page.py"
        result = manager.discover_layouts_for_template(page_file)

        assert result is not None
        assert page_file in manager._layout_registry

    def test_discover_layouts_no_layouts(self, tmp_path) -> None:
        """Test discover_layouts_for_template when no layouts exist."""
        manager = LayoutManager()

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        page_file = sub_dir / "page.py"

        result = manager.discover_layouts_for_template(page_file)

        assert result is None
        assert page_file not in manager._layout_registry

    def test_get_layout_template(self, tmp_path) -> None:
        """Test get_layout_template method."""
        manager = LayoutManager()

        # create layout structure
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>Test</h1>")

        page_file = sub_dir / "page.py"
        manager.discover_layouts_for_template(page_file)

        result = manager.get_layout_template(page_file)
        assert result is not None

    def test_get_layout_template_not_found(self, tmp_path) -> None:
        """Test get_layout_template when template not found."""
        manager = LayoutManager()

        page_file = tmp_path / "page.py"
        result = manager.get_layout_template(page_file)

        assert result is None

    def test_clear_registry(self) -> None:
        """Test clear_registry method."""
        layout_manager = LayoutManager()

        # add some data to registry
        layout_manager._layout_registry["test_path"] = "test_template"
        assert len(layout_manager._layout_registry) == 1

        # clear registry
        layout_manager.clear_registry()
        assert len(layout_manager._layout_registry) == 0


class TestLayoutIntegration:
    """Test cases for layout integration with Page class."""

    def test_page_with_layout_manager(self, page_instance) -> None:
        """Test that Page class has LayoutManager."""
        assert hasattr(page_instance, "_layout_manager")
        assert isinstance(page_instance._layout_manager, LayoutManager)

    def test_create_url_pattern_with_layout(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """Test create_url_pattern with layout inheritance."""
        # create layout structure
        layout_file = tmp_path / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        # create template.djx
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>{{ title }}</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"
        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        # Template loaded lazily at first render
        result = page_instance.render(page_file, title="Test")
        assert "Test" in result

    def test_render_with_layout_inheritance(self, page_instance, tmp_path) -> None:
        """Test rendering with layout inheritance."""
        # create layout structure
        layout_file = tmp_path / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        # create template.djx
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>{{ title }}</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"

        # discover layouts
        page_instance._layout_manager.discover_layouts_for_template(page_file)
        layout_template = page_instance._layout_manager.get_layout_template(page_file)
        page_instance.register_template(page_file, layout_template)

        # check that template contains layout content
        assert "<html><body>" in layout_template
        assert "</body></html>" in layout_template
        assert "{% block template %}" in layout_template

    def test_render_composes_template_djx_under_ancestor_layout(
        self, page_instance, tmp_path
    ) -> None:
        """Page.render wraps the sibling template.djx body through ancestor layouts."""
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance.render(page_file, title="Hi")

        assert "<html><body>" in result
        assert "<h1>Hi</h1>" in result
        assert "</body></html>" in result
        assert page_file in page_instance._template_registry

    def test_render_with_layout_template_detection(
        self, page_instance, tmp_path
    ) -> None:
        """Test render method with layout template detection."""
        # create a template that looks like a layout template but doesn't use extends
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        result = page_instance.render(page_file, title="Test")

        # should use regular template rendering
        assert result == "<h1>Test</h1>"


class TestLoadPythonModule:
    """Test _load_python_module functionality."""

    def test_load_python_module_invalid_file(self, tmp_path) -> None:
        """Test _load_python_module with invalid Python file."""
        # create an invalid Python file
        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("invalid python syntax {")

        result = _load_python_module(invalid_file)
        assert result is None

    def test_load_python_module_nonexistent_file(self, tmp_path) -> None:
        """Test _load_python_module with nonexistent file."""
        nonexistent_file = tmp_path / "nonexistent.py"

        result = _load_python_module(nonexistent_file)
        assert result is None

    def test_load_python_module_no_spec_returns_none(self, tmp_path) -> None:
        """Test _load_python_module when spec_from_file_location returns None."""
        valid_file = tmp_path / "page.py"
        valid_file.write_text("x = 1")
        with patch("importlib.util.spec_from_file_location", return_value=None):
            result = _load_python_module(valid_file)
        assert result is None

    def test_load_python_module_valid_file_returns_module(self, tmp_path) -> None:
        """Test _load_python_module with valid Python file returns the module."""
        valid_file = tmp_path / "page.py"
        valid_file.write_text("x = 42\ntemplate = '<p>{{ x }}</p>'")

        result = _load_python_module(valid_file)
        assert result is not None
        assert hasattr(result, "x")
        assert result.x == 42
        assert hasattr(result, "template")


def _make_real_request() -> HttpRequest:
    """Build a minimal `HttpRequest` usable by the unified view."""
    request = HttpRequest()
    request.method = "GET"
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    return request


class TestUnifiedViewBodyResolution:
    """`_create_unified_view` resolves the body via render > template > template.djx."""

    @pytest.fixture(autouse=True)
    def _isolate(self):
        page._template_registry.clear()
        page._template_source_mtimes.clear()
        page._layout_manager._layout_registry.clear()
        yield
        page._template_registry.clear()
        page._template_source_mtimes.clear()
        page._layout_manager._layout_registry.clear()

    def test_template_attribute_with_ancestor_layout_composes(
        self, page_instance, tmp_path
    ) -> None:
        """`template = "..."` flows through an ancestor `layout.djx`."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text('template = "<h1>attr body</h1>"')

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "<h1>attr body</h1>" in body
        assert "</body></html>" in body

    def test_render_returning_str_with_ancestor_layout_composes(
        self, page_instance, tmp_path
    ) -> None:
        """`render()` returning a string flows through the ancestor layout."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    return '<p>rendered</p>'\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "<p>rendered</p>" in body

    def test_render_returning_httpresponse_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`render()` returning HttpResponse is returned verbatim, no layout."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import HttpResponse\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponse('raw', status=201)\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert response.status_code == 201
        assert response.content == b"raw"
        assert "<html>" not in response.content.decode()

    def test_render_returning_redirect_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`HttpResponseRedirect` (an HttpResponse subclass) is returned verbatim."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import HttpResponseRedirect\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponseRedirect('/target/')\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert response.status_code == 302
        assert response["Location"] == "/target/"

    def test_render_returning_jsonresponse_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`JsonResponse` (an HttpResponse subclass) is returned verbatim."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import JsonResponse\n"
            "def render(request, **kwargs):\n"
            "    return JsonResponse({'ok': True})\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert response["Content-Type"].startswith("application/json")
        assert response.content == b'{"ok": true}'

    @pytest.mark.parametrize(
        "return_value",
        [
            "None",
            "{'x': 1}",
            "[1, 2]",
            "42",
        ],
        ids=["None", "dict", "list", "int"],
    )
    def test_render_returning_non_str_non_response_raises(
        self, page_instance, tmp_path, return_value
    ) -> None:
        """`render()` returning anything other than str/HttpResponse raises TypeError."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            f"def render(request, **kwargs):\n    return {return_value}\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        with pytest.raises(TypeError, match="must return str or HttpResponse"):
            view(_make_real_request())

    def test_render_raising_propagates(self, page_instance, tmp_path) -> None:
        """`render()` raising an exception propagates to the caller."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    raise RuntimeError('boom')\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        with pytest.raises(RuntimeError, match="boom"):
            view(_make_real_request())

    def test_priority_render_wins_over_template_attr(
        self, page_instance, tmp_path
    ) -> None:
        """When both render() and template attr exist, render() wins."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            'template = "<p>from-attr</p>"\n'
            "def render(request, **kwargs):\n"
            "    return '<p>from-render</p>'\n"
        )

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert b"from-render" in response.content
        assert b"from-attr" not in response.content

    def test_priority_template_attr_wins_over_template_djx(
        self, page_instance, tmp_path
    ) -> None:
        """When both template attr and template.djx exist, attr wins."""
        (tmp_path / "template.djx").write_text("<p>from-djx</p>")
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "<p>from-attr</p>"')

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert b"from-attr" in response.content
        assert b"from-djx" not in response.content

    def test_empty_body_with_layout_renders_layout_shell(
        self, page_instance, tmp_path
    ) -> None:
        """A page with no body source still renders the ancestor layout's shell."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("")

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "</body></html>" in body


class TestLoadStaticBodyEdgeCases:
    """`Page._load_static_body` edge cases."""

    def test_unreadable_template_djx_returns_empty(
        self, page_instance, tmp_path
    ) -> None:
        """UnicodeDecodeError on `template.djx` yields an empty body, not a crash."""
        template_djx = tmp_path / "template.djx"
        template_djx.write_bytes(b"\xff\xfe invalid utf-8")
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        assert page_instance._load_static_body(page_file, None) == ""

    def test_has_template_returns_true_when_ancestor_layout_exists(
        self, page_instance, tmp_path
    ) -> None:
        """`has_template` short-circuits to True when an ancestor layout applies."""
        (tmp_path / "layout.djx").write_text(
            "<main>{% block template %}{% endblock template %}</main>",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        page_file = sub / "page.py"
        page_file.write_text("")
        assert page_instance.has_template(page_file, module=None) is True


class TestLayoutComposeBody:
    """`LayoutTemplateLoader.compose_body` is a pure string → string wrap."""

    def test_no_layouts_returns_body_verbatim(self, tmp_path) -> None:
        """Without layout.djx the body is returned unchanged."""
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        loader = LayoutTemplateLoader()
        assert loader.compose_body("<p>hi</p>", page_file) == "<p>hi</p>"

    def test_ancestor_layout_wraps_body_in_block(self, tmp_path) -> None:
        """Without a sibling layout the body is wrapped in a `{% block template %}`."""
        (tmp_path / "layout.djx").write_text(
            "<main>{% block template %}{% endblock template %}</main>",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        page_file = sub / "page.py"
        loader = LayoutTemplateLoader()
        result = loader.compose_body("<p>body</p>", page_file)
        assert (
            result
            == "<main>{% block template %}<p>body</p>{% endblock template %}</main>"
        )

    def test_sibling_layout_substitutes_body_directly(self, tmp_path) -> None:
        """With a sibling layout the body replaces the placeholder verbatim."""
        (tmp_path / "layout.djx").write_text(
            "<section>{% block template %}{% endblock template %}</section>",
        )
        page_file = tmp_path / "page.py"
        loader = LayoutTemplateLoader()
        result = loader.compose_body("<p>body</p>", page_file)
        assert result == "<section><p>body</p></section>"


class _MdLoader(TemplateLoader):
    """Test double: render sibling `template.md` as `<article>{body}</article>`."""

    source_name = "template.md"

    def can_load(self, file_path):
        return (file_path.parent / "template.md").exists()

    def load_template(self, file_path):
        text = (file_path.parent / "template.md").read_text()
        return f"<article>{text}</article>"

    def source_path(self, file_path):
        p = file_path.parent / "template.md"
        return p if p.exists() else None


class TestCustomTemplateLoaderIntegration:
    """Custom `TemplateLoader` registered via `TEMPLATE_LOADERS` feeds `Page.render`."""

    @pytest.fixture(autouse=True)
    def _install_md_loader(self):
        import next.pages.loaders as loaders_module

        loaders_module._REGISTERED_LOADERS_CACHE = [_MdLoader()]
        page._template_registry.clear()
        page._template_source_mtimes.clear()
        yield
        loaders_module._REGISTERED_LOADERS_CACHE = None
        page._template_registry.clear()
        page._template_source_mtimes.clear()

    def test_custom_loader_body_is_rendered_through_layout(
        self, page_instance, tmp_path
    ) -> None:
        """A custom loader for `template.md` feeds `_load_static_body`."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )
        page_dir = tmp_path / "post"
        page_dir.mkdir()
        (page_dir / "template.md").write_text("hello")
        page_file = page_dir / "page.py"
        page_file.write_text("")

        body = page_instance._load_static_body(page_file, None)
        assert body == "<article>hello</article>"
        html = page_instance.render(page_file)
        assert "<html>" in html
        assert "<article>hello</article>" in html

    def test_module_template_beats_custom_loader(self, page_instance, tmp_path) -> None:
        """`module.template` attribute still wins over any registered loader."""
        (tmp_path / "template.md").write_text("ignored")
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "from-attr"')

        from next.pages.loaders import _load_python_module_memo

        module = _load_python_module_memo(page_file)
        body = page_instance._load_static_body(page_file, module)
        assert body == "from-attr"

    def test_has_template_picks_up_custom_loader(self, page_instance, tmp_path) -> None:
        """`has_template` returns True when only a custom loader can load."""
        (tmp_path / "template.md").write_text("hello")
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        assert page_instance.has_template(page_file, module=None) is True

    def test_get_template_source_paths_uses_loader_source_path(
        self, page_instance, tmp_path
    ) -> None:
        """Stale-cache detection reads `source_path` from the registered loader."""
        md = tmp_path / "template.md"
        md.write_text("body")
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        paths = page_instance._get_template_source_paths(page_file)
        assert md in paths
