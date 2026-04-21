from pathlib import Path
from unittest.mock import patch

import pytest

from next.checks import _load_python_module
from next.pages import Page, context, page
from next.pages.loaders import (
    DjxTemplateLoader,
    LayoutManager,
    LayoutTemplateLoader,
    PythonTemplateLoader,
)
from next.pages.registry import PageContextRegistry


class TestPage:
    """Test cases for Page class."""

    def test_init(self, page_instance) -> None:
        """Test Page initialization."""
        assert page_instance._template_registry == {}
        assert isinstance(page_instance._context_manager, PageContextRegistry)
        assert len(page_instance._template_loaders) == 3
        # check that all expected loaders are present
        loader_types = [type(loader) for loader in page_instance._template_loaders]
        assert PythonTemplateLoader in loader_types
        assert DjxTemplateLoader in loader_types
        assert LayoutTemplateLoader in loader_types

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

    def test_load_template_for_file_false_when_no_usable_template_source(
        self, page_instance, tmp_path
    ) -> None:
        """_load_template_for_file skips loaders that yield no content and returns False."""
        page_file = tmp_path / "page.py"
        page_file.write_text("y = 1")
        assert not page_instance._load_template_for_file(page_file)
        assert page_file not in page_instance._template_registry

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

    def test_load_template_for_file_layout_fallback(
        self, page_instance, tmp_path
    ) -> None:
        """Test _load_template_for_file with layout fallback."""
        # create layout structure
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )

        # create template.djx
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance._load_template_for_file(page_file)

        assert result is True
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
