import tempfile
from pathlib import Path
from typing import Never
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpRequest

from next.checks import (
    _has_template_or_djx,
    _load_python_module,
    check_context_functions,
    check_duplicate_url_parameters,
    check_layout_templates,
    check_missing_page_content,
)
from next.pages import (
    ContextManager,
    DjxTemplateLoader,
    LayoutManager,
    LayoutTemplateLoader,
    Page,
    PythonTemplateLoader,
    _get_context_processors,
    _import_context_processor,
    context,
    get_layout_djx_paths_for_watch,
    get_template_djx_paths_for_watch,
    page,
)
from next.urls import (
    FileRouterBackend,
    URLPatternParser,
)
from next.utils import NextStatReloader


# shared fixtures
@pytest.fixture()
def page_instance():
    """Create a fresh Page instance for each test."""
    return Page()


@pytest.fixture()
def url_parser():
    """Create a URLPatternParser instance for testing."""
    return URLPatternParser()


@pytest.fixture()
def python_template_loader():
    """Create a PythonTemplateLoader instance for testing."""
    return PythonTemplateLoader()


@pytest.fixture()
def djx_template_loader():
    """Create a DjxTemplateLoader instance for testing."""
    return DjxTemplateLoader()


@pytest.fixture()
def context_manager():
    """Create a ContextManager instance for testing."""
    return ContextManager()


@pytest.fixture()
def layout_manager():
    """Create a LayoutManager instance for testing."""
    return LayoutManager()


@pytest.fixture()
def mock_frame():
    """Mock inspect.currentframe for testing."""
    with patch("next.pages.inspect.currentframe") as mock_frame:
        yield mock_frame


@pytest.fixture()
def test_file_path():
    """Create a test file path for render tests."""
    return Path("/test/path/page.py")


@pytest.fixture()
def global_file_path():
    """Create a file path for global page tests."""
    return Path("/test/global/page.py")


@pytest.fixture()
def temp_python_file():
    """Create a temporary Python file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write('template = "test template"')
        temp_file = Path(f.name)
    yield temp_file
    temp_file.unlink()


@pytest.fixture()
def context_temp_file():
    """Create a temporary file for context decorator tests."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def test_func(): pass")
        temp_file = Path(f.name)
    yield temp_file
    temp_file.unlink()


class TestPage:
    """Test cases for Page class."""

    def test_init(self, page_instance) -> None:
        """Test Page initialization."""
        assert page_instance._template_registry == {}
        assert isinstance(page_instance._context_manager, ContextManager)
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
        func_registered, inherit = page_instance._context_manager._context_registry[
            context_temp_file
        ][expected_key]
        assert func_registered == func
        assert inherit is False

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
        func, inherit = page_instance._context_manager._context_registry[
            context_temp_file
        ]["inherited_key"]
        assert func == get_inherited_value
        assert inherit is True

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
        func, inherit = page_instance._context_manager._context_registry[
            context_temp_file
        ][None]
        assert func == get_context_data
        assert inherit is True

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
        func, inherit = page._context_manager._context_registry[global_file_path][
            "test_key"
        ]
        assert func == test_function
        assert inherit is False

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
    )
    def test_get_caller_path_error_cases(
        self, page_instance, test_case, frame_setup
    ) -> None:
        """Test _get_caller_path error cases with parametrized test cases."""
        with patch("next.pages.inspect.currentframe") as mock_frame:
            frame_setup(mock_frame)

            with pytest.raises(
                RuntimeError,
                match="Could not determine caller file path",
            ):
                page_instance._get_caller_path(1)


class TestURLPatternParser:
    """Test cases for URL pattern parsing methods."""

    @pytest.mark.parametrize(
        ("url_pattern", "expected_pattern", "expected_params"),
        [
            ("simple", "simple/", {}),
            ("user/[id]", "user/<str:id>/", {"id": "id"}),
            ("user/[int:user-id]", "user/<int:user_id>/", {"user_id": "user_id"}),
            ("profile/[[args]]", "profile/<path:args>/", {"args": "args"}),
            (
                "user/[int:id]/posts/[[args]]",
                "user/<int:id>/posts/<path:args>/",
                {"id": "id", "args": "args"},
            ),
            ("", "", {}),
        ],
        ids=[
            "simple",
            "user_id",
            "user_int_id",
            "profile_args",
            "user_id_posts_args",
            "empty",
        ],
    )
    def test_parse_url_pattern_variations(
        self,
        url_parser,
        url_pattern,
        expected_pattern,
        expected_params,
    ) -> None:
        """Test parsing URL patterns with different variations."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)
        assert pattern == expected_pattern
        assert params == expected_params

    @pytest.mark.parametrize(
        ("url_pattern", "expected_contains"),
        [
            (
                "user/[int:user-id]/posts/[slug:post-slug]/[[args]]",
                [
                    "<int:user_id>",
                    "<slug:post_slug>",
                    "<path:args>",
                    "user_id",
                    "post_slug",
                    "args",
                ],
            ),
        ],
    )
    def test_parse_url_pattern_complex(
        self,
        url_parser,
        url_pattern,
        expected_contains,
    ) -> None:
        """Test parsing complex URL pattern."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)

        for expected in expected_contains:
            if expected.startswith("<"):
                assert expected in pattern
            else:
                assert expected in params

        assert pattern.endswith("/")  # should end with slash

    @pytest.mark.parametrize(
        ("url_pattern", "pattern_contains", "params_condition"),
        [
            ("[]", ["["], lambda p: len(p) == 0 or "" in p),
            ("[[]]", ["["], lambda p: len(p) == 0 or "" in p),
        ],
    )
    def test_parse_url_pattern_edge_cases(
        self,
        url_parser,
        url_pattern,
        pattern_contains,
        params_condition,
    ) -> None:
        """Test parsing URL pattern edge cases."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)
        assert any(contains in pattern for contains in pattern_contains)
        assert params_condition(params)

    @pytest.mark.parametrize(
        ("param_string", "expected_name", "expected_type"),
        [
            ("param", "param", "str"),
            ("int:user-id", "user-id", "int"),
            ("", "", "str"),
            ("   ", "", "str"),
            (":param", "param", ""),
        ],
    )
    def test_parse_param_name_and_type_variations(
        self,
        url_parser,
        param_string,
        expected_name,
        expected_type,
    ) -> None:
        """Test parsing parameter name and type with different variations."""
        name, type_name = url_parser._parse_param_name_and_type(param_string)
        assert name == expected_name
        assert type_name == expected_type

    @pytest.mark.parametrize(
        ("url_path", "expected_params", "expected_pattern"),
        [
            (
                "user/[[profile]]/[int:user-id]/posts",
                ["profile", "user_id"],
                "user/<path:profile>/<int:user_id>/posts/",
            ),
        ],
    )
    def test_parse_url_pattern_with_args_and_params(
        self,
        url_parser,
        url_path,
        expected_params,
        expected_pattern,
    ) -> None:
        """Test parsing URL pattern with both args and regular parameters."""
        django_pattern, parameters = url_parser.parse_url_pattern(url_path)

        for param in expected_params:
            assert param in parameters
        assert django_pattern == expected_pattern

    @pytest.mark.parametrize(
        ("url_path", "expected_name"),
        [
            ("user/[int:user-id]/posts", "user_int_user_id_posts"),
            ("profile/[[args]]", "profile_args"),
            (
                "user/[int:id]/posts/[slug:post-slug]/[[args]]",
                "user_int_id_posts_slug_post_slug_args",
            ),
        ],
    )
    def test_prepare_url_name_with_colons(
        self, url_parser, url_path, expected_name
    ) -> None:
        """Test URL name preparation with colons in parameter syntax."""
        clean_name = url_parser.prepare_url_name(url_path)
        assert clean_name == expected_name
        assert ":" not in clean_name

    def test_scan_pages_directory_virtual_view_detection(self, tmp_path) -> None:
        """Test _scan_pages_directory detects virtual views (template.djx without page.py)."""
        # create a FileRouterBackend instance
        backend = FileRouterBackend()

        # create a directory structure with template.djx but no page.py
        virtual_dir = tmp_path / "virtual"
        virtual_dir.mkdir()
        template_file = virtual_dir / "template.djx"
        template_file.write_text("<h1>Virtual Page</h1>")

        # scan the directory using the instance method
        results = list(backend._scan_pages_directory(tmp_path))

        # should find the virtual page
        assert len(results) == 1
        url_path, page_path = results[0]
        assert url_path == "virtual"
        assert page_path == virtual_dir / "page.py"  # virtual page.py path


class TestPythonTemplateLoader:
    """Test cases for Python template loader."""

    @pytest.mark.parametrize(
        ("file_content", "expected_can_load", "expected_load_result"),
        [
            ('template = "Hello {{ name }}!"', True, "Hello {{ name }}!"),
            ('print("test")', False, None),
            ("invalid python syntax !!!", False, None),
        ],
        ids=["template_attr", "no_template_attr", "invalid_syntax"],
    )
    def test_can_load_and_load_template(
        self,
        python_template_loader,
        tmp_path,
        file_content,
        expected_can_load,
        expected_load_result,
    ) -> None:
        """Test can_load and load_template with different file contents."""
        page_file = tmp_path / "page.py"
        page_file.write_text(file_content)

        can_load_result = python_template_loader.can_load(page_file)
        load_result = python_template_loader.load_template(page_file)

        assert can_load_result is expected_can_load
        assert load_result == expected_load_result


class TestContextManager:
    """Test cases for ContextManager."""

    def test_init(self, context_manager) -> None:
        """Test ContextManager initialization."""
        assert context_manager._context_registry == {}

    @pytest.mark.parametrize(
        ("key", "func_return", "expected_result"),
        [
            ("test_key", lambda: "test_value", {"test_key": "test_value"}),
            (
                None,
                lambda: {"key1": "value1", "key2": "value2"},
                {"key1": "value1", "key2": "value2"},
            ),
        ],
    )
    def test_register_and_collect_context(
        self,
        context_manager,
        test_file_path,
        key,
        func_return,
        expected_result,
    ) -> None:
        """Test registering and collecting context with different key types."""
        context_manager.register_context(test_file_path, key, func_return)

        assert test_file_path in context_manager._context_registry
        assert key in context_manager._context_registry[test_file_path]
        assert context_manager._context_registry[test_file_path][key] == (
            func_return,
            False,
        )

        result = context_manager.collect_context(test_file_path)
        assert result == expected_result

    def test_collect_context_multiple_functions(
        self, context_manager, test_file_path
    ) -> None:
        """Test collecting context with multiple functions."""

        def func1() -> str:
            return "value1"

        def func2():
            return {"key2": "value2", "key3": "value3"}

        context_manager.register_context(test_file_path, "key1", func1)
        context_manager.register_context(test_file_path, None, func2)

        result = context_manager.collect_context(test_file_path)

        assert result == {"key1": "value1", "key2": "value2", "key3": "value3"}

    def test_collect_context_second_function_gets_first_via_param_name(
        self, context_manager, test_file_path
    ) -> None:
        """Second @context("key") receives first key's value when param name matches."""
        context_manager.register_context(
            test_file_path, "custom_context_var", lambda: "12345"
        )

        def landing(custom_context_var: str) -> dict[str, str]:
            return {"title": "Landing", "custom_context_var": custom_context_var}

        context_manager.register_context(test_file_path, "landing", landing)

        result = context_manager.collect_context(test_file_path)

        assert result["custom_context_var"] == "12345"
        assert result["landing"] == {
            "title": "Landing",
            "custom_context_var": "12345",
        }

    def test_collect_context_no_functions(
        self, context_manager, test_file_path
    ) -> None:
        """Test collecting context when no functions are registered."""
        result = context_manager.collect_context(test_file_path)

        assert result == {}

    def test_register_context_with_inherit_context(
        self,
        context_manager,
        test_file_path,
    ) -> None:
        """Test registering context with inherit_context=True."""

        def test_func() -> str:
            return "inherited_value"

        context_manager.register_context(
            test_file_path,
            "inherited_key",
            test_func,
            inherit_context=True,
        )

        assert test_file_path in context_manager._context_registry
        assert "inherited_key" in context_manager._context_registry[test_file_path]
        func, inherit = context_manager._context_registry[test_file_path][
            "inherited_key"
        ]
        assert func == test_func
        assert inherit is True

    def test_collect_inherited_context(self, context_manager, tmp_path) -> None:
        """Test collecting inherited context from layout directories."""
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

        # register context in layout page.py with inherit_context=True
        def layout_func() -> str:
            return "layout_value"

        context_manager.register_context(
            page_file,
            "layout_var",
            layout_func,
            inherit_context=True,
        )

        # collect context for child page
        result = context_manager.collect_context(child_page_file)

        assert "layout_var" in result
        assert result["layout_var"] == "layout_value"

    def test_collect_inherited_context_multiple_levels(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting inherited context from multiple layout levels."""
        # create nested layout structure
        root_dir = tmp_path / "root"
        root_dir.mkdir()
        root_layout = root_dir / "layout.djx"
        root_layout.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )
        root_page = root_dir / "page.py"
        root_page.write_text("")  # create empty page.py

        sub_dir = root_dir / "sub"
        sub_dir.mkdir()
        sub_layout = sub_dir / "layout.djx"
        sub_layout.write_text("<div>{% block template %}{% endblock template %}</div>")
        sub_page = sub_dir / "page.py"
        sub_page.write_text("")  # create empty page.py

        child_dir = sub_dir / "child"
        child_dir.mkdir()
        child_page = child_dir / "page.py"

        # register context in both layout levels
        def root_func() -> str:
            return "root_value"

        def sub_func() -> str:
            return "sub_value"

        context_manager.register_context(
            root_page,
            "root_var",
            root_func,
            inherit_context=True,
        )
        context_manager.register_context(
            sub_page,
            "sub_var",
            sub_func,
            inherit_context=True,
        )

        # collect context for child page
        result = context_manager.collect_context(child_page)

        assert "root_var" in result
        assert "sub_var" in result
        assert result["root_var"] == "root_value"
        assert result["sub_var"] == "sub_value"

    def test_collect_inherited_context_no_layout(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting context when no layout files exist."""
        page_file = tmp_path / "page.py"
        result = context_manager.collect_context(page_file)
        assert result == {}

    def test_collect_inherited_context_no_page_py(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting context when layout.djx exists but no page.py."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        result = context_manager.collect_context(child_page_file)
        assert result == {}

    def test_collect_inherited_context_inherit_false(
        self, context_manager, tmp_path
    ) -> None:
        """Test that context with inherit_context=False is not inherited."""
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

        # register context with inherit_context=False
        def layout_func() -> str:
            return "layout_value"

        context_manager.register_context(
            page_file,
            "layout_var",
            layout_func,
            inherit_context=False,
        )

        # collect context for child page
        result = context_manager.collect_context(child_page_file)

        assert "layout_var" not in result

    def test_collect_inherited_context_dict_return(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting inherited context with dict return (key=None)."""
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

        # register context function that returns dict with inherit_context=True
        def layout_dict_func():
            return {"inherited_key1": "value1", "inherited_key2": "value2"}

        context_manager.register_context(
            page_file,
            None,
            layout_dict_func,
            inherit_context=True,
        )

        # collect context for child page
        result = context_manager.collect_context(child_page_file)

        assert "inherited_key1" in result
        assert "inherited_key2" in result
        assert result["inherited_key1"] == "value1"
        assert result["inherited_key2"] == "value2"


class TestDjxTemplateLoader:
    """Test cases for DJX template loader."""

    @pytest.mark.parametrize(
        ("create_djx_file", "djx_content", "expected_result"),
        [
            (
                True,
                "<h1>{{ title }}</h1><p>{{ content }}</p>",
                "<h1>{{ title }}</h1><p>{{ content }}</p>",
            ),
            (False, None, None),
        ],
    )
    def test_load_djx_template(
        self,
        djx_template_loader,
        tmp_path,
        create_djx_file,
        djx_content,
        expected_result,
    ) -> None:
        """Test loading of template.djx template with different scenarios."""
        # create page.py file
        page_file = tmp_path / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file if needed
        if create_djx_file:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        # test loading
        result = djx_template_loader.load_template(page_file)

        assert result == expected_result

    @pytest.mark.parametrize(
        ("test_case", "page_content", "create_djx", "djx_content", "expected_template"),
        [
            (
                "djx_template_only",
                'print("test")',
                True,
                "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>",
                "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>",
            ),
            (
                "template_priority",
                'template = "Python template: {{ name }}"',
                True,
                "<h1>DJX template: {{ name }}</h1>",
                "Python template: {{ name }}",
            ),
        ],
    )
    def test_create_url_pattern_template_scenarios(
        self,
        page_instance,
        tmp_path,
        url_parser,
        test_case,
        page_content,
        create_djx,
        djx_content,
        expected_template,
    ) -> None:
        """Test create_url_pattern with different template scenarios."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)

        if create_djx:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        # Template is loaded lazily at first render, not at create_url_pattern
        result = page_instance.render(page_file, title="Title", name="World")
        expected_rendered = expected_template.replace("{{ title }}", "Title").replace(
            "{{ name }}", "World"
        )
        assert expected_rendered in result

    def test_render_djx_template_with_context(self, page_instance, tmp_path) -> None:
        """Test rendering template.djx template with context."""
        # create page.py file
        page_file = tmp_path / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file
        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>"
        djx_file.write_text(djx_content)

        # load template using the new approach
        djx_loader = DjxTemplateLoader()
        if djx_loader.can_load(page_file):
            template_content = djx_loader.load_template(page_file)
            if template_content:
                page_instance.register_template(page_file, template_content)

        # render with context
        result = page_instance.render(page_file, title="Welcome", name="World")

        assert result == "<h1>Welcome</h1><p>Hello World!</p>"

    def test_render_djx_template_with_django_tags(
        self, page_instance, tmp_path
    ) -> None:
        """Test rendering template.djx template with Django tags."""
        # create page.py file
        page_file = tmp_path / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file with Django tags
        djx_file = tmp_path / "template.djx"
        djx_content = """
        <h1>{{ title }}</h1>
        {% if items %}
            <ul>
            {% for item in items %}
                <li>{{ item }}</li>
            {% endfor %}
            </ul>
        {% else %}
            <p>No items</p>
        {% endif %}
        """
        djx_file.write_text(djx_content)

        # load template using the new approach
        djx_loader = DjxTemplateLoader()
        if djx_loader.can_load(page_file):
            template_content = djx_loader.load_template(page_file)
            if template_content:
                page_instance.register_template(page_file, template_content)

        # render with context
        result = page_instance.render(
            page_file,
            title="Items",
            items=["Apple", "Banana"],
        )

        assert "Items" in result
        assert "Apple" in result
        assert "Banana" in result
        assert "<li>" in result

    def test_djx_template_with_context_functions(self, page_instance, tmp_path) -> None:
        """Test template.djx template with context functions."""
        # create page.py file with context function
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context("landing")
def get_landing_data(*args, **kwargs):
    return {
        "title": "Test Title",
        "description": "Test Description"
    }
        """)

        # create template.djx file
        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ landing.title }}</h1><p>{{ landing.description }}</p>"
        djx_file.write_text(djx_content)

        # load template using the new approach
        djx_loader = DjxTemplateLoader()
        if djx_loader.can_load(page_file):
            template_content = djx_loader.load_template(page_file)
            if template_content:
                page_instance.register_template(page_file, template_content)

        # register context function
        page_instance._context_manager.register_context(
            page_file,
            "landing",
            lambda *args, **kwargs: {
                "title": "Test Title",
                "description": "Test Description",
            },
        )

        # render template
        result = page_instance.render(page_file)

        assert "<h1>Test Title</h1>" in result
        assert "<p>Test Description</p>" in result


class TestCreateUrlPatternScenarios:
    """Test cases for different URL pattern creation scenarios."""

    @pytest.mark.parametrize(
        (
            "test_case",
            "page_content",
            "create_djx",
            "djx_content",
            "url_pattern",
            "expected_pattern_name",
            "expected_template",
        ),
        [
            (
                "render_function_only",
                """
from django.http import HttpResponse

def render(request, **kwargs):
    return HttpResponse("Hello from render function!")
                """,
                False,
                None,
                "test",
                "page_test",
                None,
            ),
            (
                "template_priority",
                'template = "Python template: {{ name }}"',
                True,
                "<h1>DJX template: {{ name }}</h1>",
                "test",
                "page_test",
                "Python template: {{ name }}",
            ),
            (
                "virtual_view_djx",
                None,
                True,
                "<h1>Virtual view: {{ title }}</h1><p>{{ content }}</p>",
                "test",
                "page_test",
                "<h1>Virtual view: {{ title }}</h1><p>{{ content }}</p>",
            ),
            (
                "virtual_view_no_djx",
                None,
                False,
                None,
                "test",
                None,
                None,
            ),
            (
                "virtual_view_with_params",
                None,
                True,
                "<h1>User: {{ user_id }}</h1><p>Post: {{ post_id }}</p>",
                "user/[int:user_id]/post/[int:post_id]",
                "page_user_int_user_id_post_int_post_id",
                "<h1>User: {{ user_id }}</h1><p>Post: {{ post_id }}</p>",
            ),
        ],
    )
    def test_create_url_pattern_scenarios(
        self,
        page_instance,
        tmp_path,
        url_parser,
        test_case,
        page_content,
        create_djx,
        djx_content,
        url_pattern,
        expected_pattern_name,
        expected_template,
    ) -> None:
        """Test various create_url_pattern scenarios."""
        page_file = tmp_path / "page.py"

        # create page.py file if content provided
        if page_content:
            page_file.write_text(page_content)

        # create template.djx file if needed
        if create_djx:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern(url_pattern, page_file, url_parser)

        if expected_pattern_name:
            assert pattern is not None
            assert pattern.name == expected_pattern_name
            if expected_template:
                # Template is loaded lazily at first render
                page_instance.render(page_file)
                assert page_file in page_instance._template_registry
                assert page_instance._template_registry[page_file] == expected_template
        else:
            assert pattern is None

    def test_create_url_pattern_render_function_fallback(
        self,
        page_instance,
        tmp_path,
        url_parser,
    ) -> None:
        """Test that render function is used as fallback when no template is found."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from django.http import HttpResponse

def render(request, **kwargs):
    return HttpResponse("Fallback render function!")
        """)

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        assert pattern.name == "page_test"

    def test_create_url_pattern_virtual_view_rendering(
        self,
        page_instance,
        tmp_path,
        url_parser,
    ) -> None:
        """Test that virtual view can be rendered with context."""
        page_file = tmp_path / "page.py"
        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>"
        djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None

        # test rendering
        result = page_instance.render(page_file, title="Welcome", name="World")
        assert result == "<h1>Welcome</h1><p>Hello World!</p>"

    def test_create_url_pattern_with_context_functions(
        self,
        page_instance,
        tmp_path,
        url_parser,
    ) -> None:
        """Test create_url_pattern with context functions for virtual view."""
        page_file = tmp_path / "page.py"
        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>{{ description }}</p>"
        djx_file.write_text(djx_content)

        # register context function
        page_instance._context_manager.register_context(
            page_file,
            "title",
            lambda: "Context Title",
        )
        page_instance._context_manager.register_context(
            page_file,
            "description",
            lambda: "Context Description",
        )

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None

        # test rendering with context
        result = page_instance.render(page_file)
        assert result == "<h1>Context Title</h1><p>Context Description</p>"


class TestPageChecks:
    """Test cases for page checks functionality."""

    @pytest.mark.parametrize(
        ("page_content", "create_djx", "djx_content", "expected_result"),
        [
            ('template = "Hello {{ name }}!"', False, None, True),
            ('print("test")', True, "<h1>{{ title }}</h1>", True),
            ('print("test")', False, None, False),
            (
                """
def render(request, **kwargs):
    return "Hello World!"
            """,
                False,
                None,
                False,
            ),
            ("invalid python syntax {", False, None, False),
        ],
    )
    def test_has_template_or_djx(
        self,
        tmp_path,
        page_content,
        create_djx,
        djx_content,
        expected_result,
    ) -> None:
        """Test _has_template_or_djx with different scenarios."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)

        if create_djx:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        result = _has_template_or_djx(page_file)
        assert result is expected_result


class TestLayoutChecks:
    """Test cases for layout checks functionality."""

    def test_check_layout_templates_with_block(self, tmp_path) -> None:
        """Test check_layout_templates with proper template block."""
        # create layout file with proper block
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        # create page file
        page_file = tmp_path / "page.py"
        page_file.write_text("")

        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [("test", page_file)]
            mock_get_pages_dir.return_value = tmp_path

            warnings = check_layout_templates(None)
            assert len(warnings) == 0

    def test_check_layout_templates_without_block(self, tmp_path) -> None:
        """Test check_layout_templates without template block."""
        # create layout file without proper block
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("<html><body>No template block</body></html>")

        # create page file
        page_file = tmp_path / "page.py"
        page_file.write_text("")

        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [("test", page_file)]
            mock_get_pages_dir.return_value = tmp_path

            warnings = check_layout_templates(None)
            assert len(warnings) == 1
            assert "does not contain required {% block template %}" in warnings[0].msg

    def test_check_layout_templates_disabled(self, tmp_path) -> None:
        """Test check_layout_templates when disabled in settings."""
        with patch("next.checks.getattr") as mock_getattr:
            mock_getattr.side_effect = (
                lambda obj, attr, default: {"check_layout_template_blocks": False}
                if attr == "NEXT_PAGES_OPTIONS"
                else default
            )
            warnings = check_layout_templates(None)
            assert len(warnings) == 0


class TestMissingPageContentChecks:
    """Test cases for missing page content checks."""

    @pytest.mark.parametrize(
        (
            "test_case",
            "page_content",
            "create_template_djx",
            "template_djx_content",
            "create_layout_djx",
            "layout_djx_content",
            "expected_warnings",
        ),
        [
            (
                "with_template",
                'template = "Hello World"',
                False,
                None,
                False,
                None,
                0,
            ),
            (
                "with_render",
                'def render(request, **kwargs):\n    return "Hello World"',
                False,
                None,
                False,
                None,
                0,
            ),
            (
                "with_template_djx",
                "",
                True,
                "<h1>Hello World</h1>",
                False,
                None,
                0,
            ),
            (
                "with_layout_djx",
                "",
                False,
                None,
                True,
                "<html>{% block template %}{% endblock template %}</html>",
                0,
            ),
            (
                "no_content",
                "",
                False,
                None,
                False,
                None,
                1,
            ),
        ],
    )
    def test_check_missing_page_content_scenarios(
        self,
        tmp_path,
        test_case,
        page_content,
        create_template_djx,
        template_djx_content,
        create_layout_djx,
        layout_djx_content,
        expected_warnings,
    ) -> None:
        """Test check_missing_page_content with different content scenarios."""
        # create page file with specified content
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)

        # create template.djx file if needed for this test case
        if create_template_djx:
            template_djx = tmp_path / "template.djx"
            template_djx.write_text(template_djx_content)

        # create layout.djx file if needed for this test case
        if create_layout_djx:
            layout_djx = tmp_path / "layout.djx"
            layout_djx.write_text(layout_djx_content)

        # mock the router manager and pages directory for testing
        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [("test", page_file)]
            mock_get_pages_dir.return_value = tmp_path

            # run the check and verify the expected number of warnings
            warnings = check_missing_page_content(None)
            assert len(warnings) == expected_warnings

            # verify warning message content if warnings are expected
            if expected_warnings > 0:
                assert "has no content" in warnings[0].msg

    def test_check_missing_page_content_disabled(self, tmp_path) -> None:
        """Test check_missing_page_content when disabled in settings."""
        with patch("next.checks.getattr") as mock_getattr:
            mock_getattr.side_effect = (
                lambda obj, attr, default: {"check_missing_page_content": False}
                if attr == "NEXT_PAGES_OPTIONS"
                else default
            )
            warnings = check_missing_page_content(None)
            assert len(warnings) == 0


class TestDuplicateUrlParametersChecks:
    """Test cases for duplicate URL parameters checks."""

    @pytest.mark.parametrize(
        ("test_case", "url_patterns", "expected_errors", "expected_error_msg"),
        [
            (
                "no_duplicates",
                [("user/[id]/post/[slug]", "page_file")],
                0,
                None,
            ),
            (
                "with_duplicates",
                [("user/[id]/[id]", "page_file")],
                1,
                "duplicate parameter names",
            ),
        ],
    )
    def test_check_duplicate_url_parameters_scenarios(
        self,
        tmp_path,
        test_case,
        url_patterns,
        expected_errors,
        expected_error_msg,
    ) -> None:
        """Test check_duplicate_url_parameters with different URL pattern scenarios."""
        # create page file
        page_file = tmp_path / "page.py"
        page_file.write_text("")

        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [
                (pattern, page_file) for pattern, _ in url_patterns
            ]
            mock_get_pages_dir.return_value = tmp_path

            errors = check_duplicate_url_parameters(None)
            assert len(errors) == expected_errors

            if expected_errors > 0 and expected_error_msg:
                assert expected_error_msg in errors[0].msg
                if "duplicate parameter names" in expected_error_msg:
                    assert "id" in errors[0].msg

    def test_check_duplicate_url_parameters_disabled(self, tmp_path) -> None:
        """Test check_duplicate_url_parameters when disabled in settings."""
        with patch("next.checks.getattr") as mock_getattr:
            mock_getattr.side_effect = (
                lambda obj, attr, default: {"check_duplicate_url_parameters": False}
                if attr == "NEXT_PAGES_OPTIONS"
                else default
            )
            errors = check_duplicate_url_parameters(None)
            assert len(errors) == 0


class TestContextFunctionsChecks:
    """Test cases for context functions checks."""

    def test_check_context_functions_valid_dict_return(self, tmp_path) -> None:
        """Test check_context_functions with valid dict return."""
        # create page file with valid context function
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
def get_context_data():
    return {"key": "value"}
        """)

        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [("test", page_file)]
            mock_get_pages_dir.return_value = tmp_path

            # mock context manager
            mock_context_manager = MagicMock()
            mock_context_manager._context_registry = {
                page_file: {None: (lambda: {"key": "value"}, False)},
            }
            mock_router._context_manager = mock_context_manager

            errors = check_context_functions(None)
            assert len(errors) == 0

    def test_check_context_functions_invalid_return_type(self, tmp_path) -> None:
        """Test check_context_functions with invalid return type."""
        # create page file with invalid context function
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
def get_context_data():
    return "not a dict"
        """)

        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [("test", page_file)]
            mock_get_pages_dir.return_value = tmp_path

            errors = check_context_functions(None)
            assert len(errors) == 1
            assert "must return a dictionary" in errors[0].msg
            assert "str" in errors[0].msg

    def test_check_context_functions_with_key_not_checked(self, tmp_path) -> None:
        """Test check_context_functions ignores functions with key."""
        # create page file with context function with key
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context("my_key")
def get_context_data():
    return "not a dict but with key"
        """)

        with (
            patch("next.checks.RouterManager") as mock_router_manager,
            patch("next.checks._get_pages_directory") as mock_get_pages_dir,
        ):
            mock_router = mock_router_manager.return_value
            mock_router._reload_config.return_value = None
            mock_router._routers = [mock_router]
            mock_router.pages_dir = "pages"
            mock_router.app_dirs = True
            mock_router._scan_pages_directory.return_value = [("test", page_file)]
            mock_get_pages_dir.return_value = tmp_path

            # mock context manager
            mock_context_manager = MagicMock()
            mock_context_manager._context_registry = {
                page_file: {"my_key": (lambda: "not a dict but with key", False)},
            }
            mock_router._context_manager = mock_context_manager

            errors = check_context_functions(None)
            assert len(errors) == 0

    def test_check_context_functions_disabled(self, tmp_path) -> None:
        """Test check_context_functions when disabled in settings."""
        with patch("next.checks.getattr") as mock_getattr:
            mock_getattr.side_effect = (
                lambda obj, attr, default: {"check_context_return_types": False}
                if attr == "NEXT_PAGES_OPTIONS"
                else default
            )
            errors = check_context_functions(None)
            assert len(errors) == 0


class TestLayoutTemplateLoader:
    """Test cases for LayoutTemplateLoader."""

    @pytest.mark.parametrize(
        ("create_layout", "create_template", "expected_can_load"),
        [
            (True, True, True),
            (False, True, False),
            (True, False, True),
            (False, False, False),
        ],
        ids=["layout_and_template", "template_only", "layout_only", "neither"],
    )
    def test_can_load_with_layout_files(
        self,
        tmp_path,
        create_layout,
        create_template,
        expected_can_load,
    ) -> None:
        """Test can_load with different layout and template combinations."""
        loader = LayoutTemplateLoader()

        # create nested directory structure
        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        # create layout.djx in parent directory if needed
        if create_layout:
            layout_file = tmp_path / "layout.djx"
            layout_file.write_text(
                "<html><body>{% block template %}{% endblock template %}</body></html>",
            )

        # create template.djx in nested directory if needed
        if create_template:
            template_file = sub_dir / "template.djx"
            template_file.write_text("<h1>Test Content</h1>")

        # test with page.py path in nested directory
        page_file = sub_dir / "page.py"

        result = loader.can_load(page_file)
        assert result is expected_can_load

    def test_get_additional_layout_files_with_next_pages_config(self, tmp_path) -> None:
        """Test _get_additional_layout_files with NEXT_PAGES configuration."""
        loader = LayoutTemplateLoader()

        # create layout file
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        # mock NEXT_PAGES configuration
        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(tmp_path),
                },
            },
        ]

        with patch("next.pages.settings.NEXT_PAGES", config, create=True):
            result = loader._get_additional_layout_files()

        assert len(result) == 1
        assert layout_file in result

    @pytest.mark.parametrize(
        ("test_case", "config", "expected_result"),
        [
            (
                "invalid_config",
                [
                    "invalid_config",  # not a dict
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "APP_DIRS": False,
                        "OPTIONS": {
                            "PAGES_DIR": "/nonexistent/path",
                        },
                    },
                ],
                [],
            ),
            (
                "app_dirs_true",
                [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "APP_DIRS": True,
                    },
                ],
                [],
            ),
        ],
    )
    def test_get_additional_layout_files_scenarios(
        self,
        tmp_path,
        test_case,
        config,
        expected_result,
    ) -> None:
        """Test _get_additional_layout_files with different configuration scenarios."""
        loader = LayoutTemplateLoader()

        with patch("next.pages.settings.NEXT_PAGES", config, create=True):
            result = loader._get_additional_layout_files()

        assert result == expected_result

    @pytest.mark.parametrize(
        ("test_case", "config", "expected_list"),
        [
            (
                "with_pages_dir",
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": False,
                    "OPTIONS": {
                        "PAGES_DIR": "test_dir",
                    },
                },
                ["test_dir"],
            ),
            (
                "with_app_dirs",
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                },
                [],
            ),
            (
                "no_options",
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": False,
                },
                [],
            ),
        ],
    )
    def test_get_pages_dirs_for_config_scenarios(
        self,
        tmp_path,
        test_case,
        config,
        expected_list,
    ) -> None:
        """Test _get_pages_dirs_for_config with different configuration scenarios."""
        loader = LayoutTemplateLoader()

        if test_case == "with_pages_dir":
            config["OPTIONS"]["PAGES_DIR"] = str(tmp_path)
            expected_list = [Path(tmp_path)]

        result = loader._get_pages_dirs_for_config(config)
        assert result == expected_list

    def test_get_pages_dirs_for_config_pages_dirs_not_list(self, tmp_path) -> None:
        """Test _get_pages_dirs_for_config when PAGES_DIRS is not a list returns []."""
        loader = LayoutTemplateLoader()
        config = {
            "OPTIONS": {"PAGES_DIRS": "not-a-list"},
        }
        result = loader._get_pages_dirs_for_config(config)
        assert result == []

    def test_get_pages_dirs_for_config_pages_dirs_list(self, tmp_path) -> None:
        """Test _get_pages_dirs_for_config when PAGES_DIRS is a list returns paths."""
        loader = LayoutTemplateLoader()
        config = {"OPTIONS": {"PAGES_DIRS": [str(tmp_path)]}}
        result = loader._get_pages_dirs_for_config(config)
        assert len(result) == 1
        assert result[0] == Path(tmp_path)

    @pytest.mark.parametrize(
        (
            "test_case",
            "create_layout",
            "create_template",
            "template_content",
            "expected_result",
        ),
        [
            (
                "with_local_layout",
                True,
                True,
                "<h1>Test Content</h1>",
                "<h1>Test Content</h1>",
            ),
            (
                "without_local_layout",
                False,
                True,
                "<h1>Test Content</h1>",
                "{% block template %}<h1>Test Content</h1>{% endblock template %}",
            ),
            (
                "no_template_file",
                False,
                False,
                None,
                "{% block template %}{% endblock template %}",
            ),
        ],
    )
    def test_wrap_in_template_block_scenarios(
        self,
        tmp_path,
        test_case,
        create_layout,
        create_template,
        template_content,
        expected_result,
    ) -> None:
        """Test _wrap_in_template_block with different file scenarios."""
        loader = LayoutTemplateLoader()

        # create layout file if needed
        if create_layout:
            layout_file = tmp_path / "layout.djx"
            layout_file.write_text("layout content")

        # create template file if needed
        if create_template:
            template_file = tmp_path / "template.djx"
            template_file.write_text(template_content)

        page_file = tmp_path / "page.py"
        result = loader._wrap_in_template_block(page_file)

        assert result == expected_result

    def test_find_layout_files_with_duplicate_additional_layouts(
        self, tmp_path
    ) -> None:
        """Test _find_layout_files when additional layouts are already in local hierarchy."""
        loader = LayoutTemplateLoader()

        # create layout file
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        # create template file
        template_file = tmp_path / "template.djx"
        template_file.write_text("template content")

        page_file = tmp_path / "page.py"

        # mock NEXT_PAGES configuration pointing to the same directory
        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(tmp_path),
                },
            },
        ]

        with patch("next.pages.settings.NEXT_PAGES", config, create=True):
            result = loader._find_layout_files(page_file)

        # should not duplicate the layout file
        assert result is not None
        assert len(result) == 1
        assert layout_file in result

    def test_get_additional_layout_files_with_duplicate_layouts(self, tmp_path) -> None:
        """Test _get_additional_layout_files with duplicate layout files."""
        loader = LayoutTemplateLoader()

        # create layout file
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        # mock NEXT_PAGES configuration with same directory twice
        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(tmp_path),
                },
            },
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(tmp_path),
                },
            },
        ]

        with patch("next.pages.settings.NEXT_PAGES", config, create=True):
            result = loader._get_additional_layout_files()

        # should not duplicate the layout file
        assert len(result) == 1
        assert layout_file in result

    def test_find_layout_files_with_additional_layouts_already_present(
        self, tmp_path
    ) -> None:
        """Test _find_layout_files when additional layouts are already in layout_files."""
        loader = LayoutTemplateLoader()

        # create layout file in parent directory (local hierarchy)
        parent_dir = tmp_path / "parent"
        parent_dir.mkdir()
        local_layout = parent_dir / "layout.djx"
        local_layout.write_text("local layout")

        # create template file in child directory
        child_dir = parent_dir / "child"
        child_dir.mkdir()
        template_file = child_dir / "template.djx"
        template_file.write_text("template content")

        page_file = child_dir / "page.py"

        # mock NEXT_PAGES configuration pointing to parent directory
        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(parent_dir),
                },
            },
        ]

        with patch("next.pages.settings.NEXT_PAGES", config, create=True):
            result = loader._find_layout_files(page_file)

        # should not duplicate the layout file (it's already in local hierarchy)
        assert result is not None
        assert len(result) == 1
        assert local_layout in result

    def test_find_layout_files_with_different_additional_layouts(
        self, tmp_path
    ) -> None:
        """Test _find_layout_files when additional layouts are different from local ones."""
        loader = LayoutTemplateLoader()

        # create local layout file
        local_layout = tmp_path / "layout.djx"
        local_layout.write_text("local layout")

        # create template file
        template_file = tmp_path / "template.djx"
        template_file.write_text("template content")

        page_file = tmp_path / "page.py"

        # create additional layout in different directory
        additional_dir = tmp_path / "additional"
        additional_dir.mkdir()
        additional_layout = additional_dir / "layout.djx"
        additional_layout.write_text("additional layout")

        # mock NEXT_PAGES configuration pointing to additional directory
        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(additional_dir),
                },
            },
        ]

        with patch("next.pages.settings.NEXT_PAGES", config, create=True):
            result = loader._find_layout_files(page_file)

        # should include both local and additional layouts
        assert result is not None
        assert len(result) == 2
        assert local_layout in result
        assert additional_layout in result

    def test_load_template_with_single_layout(self, tmp_path) -> None:
        """Test load_template with single layout file."""
        loader = LayoutTemplateLoader()

        # create layout.djx
        layout_file = tmp_path / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        # create template.djx
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>Test Content</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"
        result = loader.load_template(page_file)

        assert result is not None
        assert template_content in result
        # should contain layout content
        assert "<html><body>" in result
        assert "</body></html>" in result
        # should contain template block
        assert "{% block template %}" in result

    def test_load_template_with_multiple_layouts(self, tmp_path) -> None:
        """Test load_template with multiple layout files in hierarchy."""
        loader = LayoutTemplateLoader()

        # create root layout
        root_layout = tmp_path / "layout.djx"
        root_layout.write_text(
            "<html><head><title>Root</title></head><body>{% block template %}{% endblock template %}</body></html>",
        )

        # create sub layout
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        sub_layout = sub_dir / "layout.djx"
        sub_layout.write_text(
            "<div class='sub-layout'>{% block template %}{% endblock template %}</div>",
        )

        # create template
        nested_dir = sub_dir / "nested"
        nested_dir.mkdir()
        template_file = nested_dir / "template.djx"
        template_content = "<h1>Test Content</h1>"
        template_file.write_text(template_content)

        page_file = nested_dir / "page.py"
        result = loader.load_template(page_file)

        assert result is not None
        assert template_content in result
        # should contain both layouts
        assert "<html><head><title>Root</title></head>" in result
        assert "<div class='sub-layout'>" in result
        # should contain template block
        assert "{% block template %}" in result

    def test_load_template_without_template_djx(self, tmp_path) -> None:
        """Test load_template when template.djx doesn't exist."""
        loader = LayoutTemplateLoader()

        # create layout.djx
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
        )

        # create page.py without template.djx
        page_file = tmp_path / "page.py"

        result = loader.load_template(page_file)

        assert result is not None
        # should contain layout content
        assert "<html><body>" in result
        assert "</body></html>" in result
        # should contain empty template block
        assert "{% block template %}{% endblock template %}" in result

    def test_load_template_layout_accepts_unnamed_endblock(self, tmp_path) -> None:
        """Compose works when layout uses {% endblock %} instead of {% endblock template %}."""
        loader = LayoutTemplateLoader()
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock %}</body></html>",
        )
        page_file = tmp_path / "page.py"
        result = loader.load_template(page_file)
        assert result is not None
        assert "<html><body>" in result
        assert "</body></html>" in result
        assert "{% block template %}" in result
        # default content uses named endblock; layout used unnamed
        assert "{% block template %}{% endblock template %}" in result

    def test_find_layout_files(self, tmp_path) -> None:
        """Test _find_layout_files method."""
        loader = LayoutTemplateLoader()

        # create nested structure with layouts
        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        # create layouts at different levels
        root_layout = tmp_path / "layout.djx"
        root_layout.write_text("root layout")

        sub_layout = tmp_path / "sub" / "layout.djx"
        sub_layout.write_text("sub layout")

        page_file = sub_dir / "page.py"
        layout_files = loader._find_layout_files(page_file)

        assert layout_files is not None
        assert len(layout_files) == 2
        assert sub_layout in layout_files  # closest first
        assert root_layout in layout_files

    def test_compose_layout_hierarchy_exception_handling(self, tmp_path) -> None:
        """Test _compose_layout_hierarchy handles exceptions gracefully."""
        loader = LayoutTemplateLoader()

        # create a layout file that will cause an exception when read
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("test")

        # create template file
        template_file = tmp_path / "template.djx"
        template_file.write_text("test")

        # mock the read_text method to raise an exception
        with patch("pathlib.Path.read_text", side_effect=OSError("Mocked error")):
            # test that exception is handled gracefully
            result = loader._compose_layout_hierarchy("test content", [layout_file])
            assert (
                result == "test content"
            )  # should return original content when exception occurs

    def test_load_template_no_layout_files(self, tmp_path) -> None:
        """Test load_template when no layout files exist."""
        loader = LayoutTemplateLoader()

        # create a page file without layout files
        page_file = tmp_path / "page.py"
        page_file.write_text("template = 'test'")

        result = loader.load_template(page_file)
        assert result is None


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


class TestContextProcessors:
    """Test context_processors functionality."""

    def test_get_context_processors_empty_config(self, page_instance) -> None:
        """Test _get_context_processors with empty NEXT_PAGES config."""
        with (
            patch("django.conf.settings.NEXT_PAGES", [], create=True),
            patch("django.conf.settings.TEMPLATES", [], create=True),
        ):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_next_pages_not_list(self, page_instance) -> None:
        """When NEXT_PAGES is not a list, treat as no config."""
        with (
            patch("django.conf.settings.NEXT_PAGES", {}, create=True),
            patch("django.conf.settings.TEMPLATES", [], create=True),
        ):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_no_context_processors(self, page_instance) -> None:
        """Test _get_context_processors with NEXT_PAGES config but no context_processors."""
        config = [{"BACKEND": "next.urls.FileRouterBackend", "OPTIONS": {}}]
        with (
            patch("django.conf.settings.NEXT_PAGES", config, create=True),
            patch("django.conf.settings.TEMPLATES", [], create=True),
        ):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_inherits_from_templates(
        self, page_instance
    ) -> None:
        """Test _get_context_processors inherits from TEMPLATES when not in NEXT_PAGES."""

        def test_processor(request):
            return {"test_var": "test_value"}

        def auth_processor(request):
            return {"user": MagicMock()}

        # Mock TEMPLATES with context_processors
        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "context_processors": [
                        "test_app.context_processors.test_processor",
                        "test_app.context_processors.auth_processor",
                    ],
                },
            },
        ]

        # NEXT_PAGES without context_processors
        next_pages_config = [{"BACKEND": "next.urls.FileRouterBackend", "OPTIONS": {}}]

        with patch("next.pages.import_string") as mock_import:
            mock_import.side_effect = [test_processor, auth_processor]

            with (
                patch("django.conf.settings.TEMPLATES", templates_config, create=True),
                patch(
                    "django.conf.settings.NEXT_PAGES", next_pages_config, create=True
                ),
            ):
                processors = _get_context_processors()
                # Should inherit from TEMPLATES
                assert len(processors) == 2
                assert processors[0] == test_processor
                assert processors[1] == auth_processor

    def test_get_context_processors_merges_next_pages_and_templates(
        self, page_instance
    ) -> None:
        """When both NEXT_PAGES and TEMPLATES set context_processors, both are merged (NEXT_PAGES first)."""

        def template_processor(request):
            return {"template_var": "template_value"}

        def next_pages_processor(request):
            return {"next_var": "next_value"}

        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "context_processors": [
                        "test_app.context_processors.template_processor",
                    ],
                },
            },
        ]
        next_pages_config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "OPTIONS": {
                    "context_processors": [
                        "test_app.context_processors.next_pages_processor",
                    ],
                },
            }
        ]

        with patch("next.pages.import_string") as mock_import:
            mock_import.side_effect = [next_pages_processor, template_processor]
            with (
                patch("django.conf.settings.TEMPLATES", templates_config, create=True),
                patch(
                    "django.conf.settings.NEXT_PAGES", next_pages_config, create=True
                ),
            ):
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == next_pages_processor
                assert processors[1] == template_processor

    def test_get_context_processors_deduplicates_by_path(self, page_instance) -> None:
        """Same path in NEXT_PAGES and TEMPLATES appears once (first occurrence wins)."""
        shared_path = "test_app.context_processors.shared_processor"

        def shared_processor(request):
            return {"shared": True}

        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"context_processors": [shared_path]},
            },
        ]
        next_pages_config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "OPTIONS": {"context_processors": [shared_path]},
            },
        ]
        with (
            patch("next.pages.import_string", return_value=shared_processor),
            patch("django.conf.settings.TEMPLATES", templates_config, create=True),
            patch("django.conf.settings.NEXT_PAGES", next_pages_config, create=True),
        ):
            processors = _get_context_processors()
            assert len(processors) == 1
            assert processors[0] == shared_processor

    def test_get_context_processors_fallback_empty_templates(
        self, page_instance
    ) -> None:
        """With empty TEMPLATES and no NEXT_PAGES processors, result is empty."""
        with (
            patch("django.conf.settings.TEMPLATES", [], create=True),
            patch("django.conf.settings.NEXT_PAGES", [], create=True),
        ):
            result = _get_context_processors()
            assert result == []

    def test_get_context_processors_fallback_non_list(self, page_instance) -> None:
        """When TEMPLATES context_processors is not a list, fallback yields empty."""
        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"context_processors": "not_a_list"},
            }
        ]
        with (
            patch("django.conf.settings.TEMPLATES", templates_config, create=True),
            patch("django.conf.settings.NEXT_PAGES", [], create=True),
        ):
            result = _get_context_processors()
            assert result == []

    def test_get_context_processors_with_valid_processors(self, page_instance) -> None:
        """Test _get_context_processors with valid context processors."""

        def test_processor(request):
            return {"test_var": "test_value"}

        def another_processor(request):
            return {"another_var": "another_value"}

        # mock the import_string function to return our test processors
        with patch("next.pages.import_string") as mock_import:
            mock_import.side_effect = [test_processor, another_processor]

            config = [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "OPTIONS": {
                        "context_processors": [
                            "test_app.context_processors.test_processor",
                            "test_app.context_processors.another_processor",
                        ],
                    },
                },
            ]

            with patch("django.conf.settings.NEXT_PAGES", config, create=True):
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == test_processor
                assert processors[1] == another_processor

    def test_get_context_processors_with_invalid_processor(self, page_instance) -> None:
        """Test _get_context_processors with invalid processor path."""
        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "OPTIONS": {
                    "context_processors": [
                        "invalid.module.path",
                        "django.template.context_processors.request",
                    ],
                },
            },
        ]

        with (
            patch("django.conf.settings.NEXT_PAGES", config, create=True),
            patch("next.pages.import_string") as mock_import,
            patch("next.pages.logger.warning") as mock_warning,
        ):
            mock_import.side_effect = [
                ImportError("No module named 'invalid'"),
                lambda request: {"request": request},
            ]
            processors = _get_context_processors()
            # filter out the mock logger from processors
            real_processors = [
                p for p in processors if callable(p) and not hasattr(p, "_mock_name")
            ]
            assert len(real_processors) == 1
            # check that warning was logged
            mock_warning.assert_called_once()

    def test_import_context_processor_non_callable(self, page_instance) -> None:
        """Test _import_context_processor with non-callable import."""
        with patch("next.pages.import_string") as mock_import:
            mock_import.return_value = (
                "not a callable"  # return string instead of function
            )

            processor = _import_context_processor("some.module.path")
            assert processor is None

    def test_render_with_context_processors(self, page_instance, tmp_path) -> None:
        """Test render method with context_processors."""
        # create a test template
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ request_var }}</p>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        def test_processor(request):
            return {"request_var": "from_processor"}

        # mock _get_context_processors to return our test processor
        with patch("next.pages._get_context_processors", return_value=[test_processor]):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            # should include both template variables and context processor variables
            assert "Test Title" in result
            assert "from_processor" in result

    def test_render_without_request_object(self, page_instance, tmp_path) -> None:
        """Test render method without request object (should use regular Context)."""
        # create a test template
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        def test_processor(request):
            return {"request_var": "from_processor"}

        # mock _get_context_processors to return our test processor
        with patch("next.pages._get_context_processors", return_value=[test_processor]):
            result = page_instance.render(page_file, title="Test Title")

            # should only include template variables, not context processor variables
            assert result == "<h1>Test Title</h1>"
            assert "from_processor" not in result

    def test_render_without_context_processors(self, page_instance, tmp_path) -> None:
        """Test render method without context_processors (should use regular Context)."""
        # create a test template
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        # mock _get_context_processors to return empty list
        with patch("next.pages._get_context_processors", return_value=[]):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            # should use regular Context, not RequestContext
            assert result == "<h1>Test Title</h1>"

    def test_render_with_context_processor_error(self, page_instance, tmp_path) -> None:
        """Test render method with context processor that raises an exception."""
        # create a test template
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ good_var }}</p>"
        page_instance.register_template(page_file, template_str)

        # create a mock request
        mock_request = HttpRequest()
        mock_request.META = {}

        def error_processor(request) -> Never:
            msg = "Test error"
            raise ValueError(msg)

        def good_processor(request):
            return {"good_var": "good_value"}

        # mock _get_context_processors to return processors with one that errors
        with (
            patch(
                "next.pages._get_context_processors",
                return_value=[error_processor, good_processor],
            ),
            patch("next.pages.logger") as mock_logger,
        ):
            result = page_instance.render(
                page_file,
                mock_request,
                title="Test Title",
            )

            # should include good processor data but not error_var
            assert "Test Title" in result
            assert "good_value" in result
            # check that error was logged
            mock_logger.warning.assert_called_once()

    def test_render_with_context_processor_non_dict_return(
        self,
        page_instance,
        tmp_path,
    ) -> None:
        """Test render method with context processor that returns non-dict."""
        # create a test template
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ good_var }}</p>"
        page_instance.register_template(page_file, template_str)

        # create a mock request
        mock_request = HttpRequest()
        mock_request.META = {}

        def non_dict_processor(request) -> str:
            return "not a dict"

        def good_processor(request):
            return {"good_var": "good_value"}

        # mock _get_context_processors to return processors with one that returns non-dict
        with patch(
            "next.pages._get_context_processors",
            return_value=[non_dict_processor, good_processor],
        ):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            # should include good processor data but ignore non-dict return
            assert "Test Title" in result
            assert "good_value" in result


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


class TestPageCreateUrlPattern:
    """Test Page create_url_pattern functionality."""

    def test_create_regular_page_pattern_no_module(
        self, page_instance, tmp_path
    ) -> None:
        """Test _create_regular_page_pattern when module cannot be loaded."""
        # create an invalid page file
        page_file = tmp_path / "page.py"
        page_file.write_text("invalid python syntax {")

        url_parser = URLPatternParser()
        django_pattern, parameters = url_parser.parse_url_pattern("test")
        clean_name = url_parser.prepare_url_name("test")

        result = page_instance._create_regular_page_pattern(
            page_file,
            django_pattern,
            parameters,
            clean_name,
        )
        assert result is None

    def test_create_regular_page_pattern_no_template_no_render(
        self,
        page_instance,
        tmp_path,
    ) -> None:
        """Test _create_regular_page_pattern when no template and no render function."""
        # create a page file without template or render function
        page_file = tmp_path / "page.py"
        page_file.write_text("def other_function(): pass")

        url_parser = URLPatternParser()
        django_pattern, parameters = url_parser.parse_url_pattern("test")
        clean_name = url_parser.prepare_url_name("test")

        result = page_instance._create_regular_page_pattern(
            page_file,
            django_pattern,
            parameters,
            clean_name,
        )
        assert result is None


class TestGetLayoutDjxPathsForWatch:
    """Tests for get_layout_djx_paths_for_watch()."""

    def test_returns_layout_djx_paths_under_pages_dirs(self, tmp_path) -> None:
        """Returns resolved paths of all layout.djx under given pages dirs."""
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "layout.djx").write_text("<div>a</div>")
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "layout.djx").write_text("<div>b</div>")
        with patch("next.urls.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert len(result) == 2
        parent_names = {p.parent.name for p in result}
        assert parent_names == {"a", "b"}
        assert all(p.name == "layout.djx" for p in result)

    def test_returns_empty_when_no_layout_djx(self, tmp_path) -> None:
        """Returns empty set when no layout.djx under pages dirs."""
        with patch("next.urls.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert result == set()

    def test_swallows_oserror_on_rglob_layout(self, tmp_path) -> None:
        """When rglob raises OSError (e.g. permission), log and return partial result."""
        with (
            patch("next.urls.get_pages_directories_for_watch") as mock_watch,
            patch.object(Path, "rglob", side_effect=OSError(13, "Permission denied")),
        ):
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert result == set()


class TestGetTemplateDjxPathsForWatch:
    """Tests for get_template_djx_paths_for_watch()."""

    def test_returns_template_djx_paths_under_pages_dirs(self, tmp_path) -> None:
        """Returns resolved paths of all template.djx under given pages dirs."""
        (tmp_path / "x").mkdir()
        (tmp_path / "x" / "template.djx").write_text("x")
        (tmp_path / "x" / "y").mkdir()
        (tmp_path / "x" / "y" / "template.djx").write_text("y")
        with patch("next.urls.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert len(result) == 2
        assert all(p.name == "template.djx" for p in result)
        parent_names = {p.parent.name for p in result}
        assert parent_names == {"x", "y"}

    def test_returns_empty_when_no_template_djx(self, tmp_path) -> None:
        """Returns empty set when no template.djx under pages dirs."""
        with patch("next.urls.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert result == set()

    def test_swallows_oserror_on_rglob_template(self, tmp_path) -> None:
        """When rglob raises OSError (e.g. permission), log and return partial result."""
        with (
            patch("next.urls.get_pages_directories_for_watch") as mock_watch,
            patch.object(Path, "rglob", side_effect=OSError(13, "Permission denied")),
        ):
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert result == set()


class TestLayoutTemplateWatchReload:
    """Tests that NextStatReloader triggers reload when layout/template set changes."""

    def test_tick_notify_when_layout_set_grows(self) -> None:
        """When a new layout.djx appears (and dir has a page), notify_file_changed."""
        reloader = NextStatReloader()
        new_layout = Path("/fake/pages/simple/layout.djx").resolve()
        page_under_layout = Path("/fake/pages/simple/page.py").resolve()
        call_count = [0]

        def layout_side_effect():
            call_count[0] += 1
            return set() if call_count[0] == 1 else {new_layout}

        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                return_value=[Path("/fake/pages")],
            ),
            patch(
                "next.utils._scan_pages_directory",
                side_effect=lambda _: iter([("simple", page_under_layout)]),
            ),
            patch(
                "next.utils.get_layout_djx_paths_for_watch",
                side_effect=layout_side_effect,
            ),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)
            next(gen)
            mock_notify.assert_called_once_with(new_layout)

    def test_tick_notify_when_layout_set_shrinks(self) -> None:
        """When a layout.djx is deleted (and dir had a page), notify_file_changed."""
        reloader = NextStatReloader()
        deleted_layout = Path("/fake/pages/simple/layout.djx").resolve()
        page_under_layout = Path("/fake/pages/simple/page.py").resolve()
        call_count = [0]

        def layout_side_effect():
            call_count[0] += 1
            return {deleted_layout} if call_count[0] == 1 else set()

        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                return_value=[Path("/fake/pages")],
            ),
            patch(
                "next.utils._scan_pages_directory",
                side_effect=lambda _: iter([("simple", page_under_layout)]),
            ),
            patch(
                "next.utils.get_layout_djx_paths_for_watch",
                side_effect=layout_side_effect,
            ),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)
            next(gen)
            mock_notify.assert_called_once_with(deleted_layout)

    def test_tick_notify_when_template_set_shrinks(self) -> None:
        """When a template.djx is deleted, notify_file_changed is called."""
        reloader = NextStatReloader()
        deleted_template = Path("/fake/pages/simple/template.djx").resolve()
        call_count = [0]

        def template_side_effect():
            call_count[0] += 1
            return {deleted_template} if call_count[0] == 1 else set()

        with (
            patch("next.utils.get_pages_directories_for_watch", return_value=[]),
            patch("next.utils._scan_pages_directory", return_value=iter([])),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch(
                "next.utils.get_template_djx_paths_for_watch",
                side_effect=template_side_effect,
            ),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)
            next(gen)
            mock_notify.assert_called_once_with(deleted_template)
