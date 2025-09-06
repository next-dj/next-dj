import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from next.pages import Page, context, page


class TestPage:
    @pytest.fixture
    def page_instance(self):
        """Create a fresh Page instance for each test."""
        return Page()

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('template = "test template"')
            temp_file = Path(f.name)
        yield temp_file
        temp_file.unlink()

    @pytest.fixture
    def mock_frame(self):
        """Mock inspect.currentframe for testing."""
        with patch("next.pages.inspect.currentframe") as mock_frame:
            yield mock_frame

    def test_init(self, page_instance):
        """Test Page initialization."""
        assert page_instance._template_registry == {}
        assert page_instance._context_registry == {}

    def test_register_template_direct(self, page_instance):
        """Test register_template method with direct file path."""
        file_path = Path("/test/path/page.py")
        template_str = "Hello {{ name }}!"

        page_instance.register_template(file_path, template_str)

        assert file_path in page_instance._template_registry
        assert page_instance._template_registry[file_path] == template_str

    @pytest.fixture
    def context_temp_file(self):
        """Create a temporary file for context decorator tests."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def test_func(): pass")
            temp_file = Path(f.name)
        yield temp_file
        temp_file.unlink()

    @pytest.mark.parametrize(
        "decorator_type,expected_key,frame_chain",
        [
            ("with_key", "user_name", "f_back"),
            ("without_key", None, "f_back.f_back"),
            ("without_parentheses", None, "f_back.f_back"),
        ],
    )
    def test_context_decorator_variations(
        self,
        page_instance,
        context_temp_file,
        mock_frame,
        decorator_type,
        expected_key,
        frame_chain,
    ):
        """Test context decorator with different variations."""
        # set up the frame chain based on the test case
        frame = mock_frame.return_value
        for attr in frame_chain.split("."):
            frame = getattr(frame, attr)
        frame.f_globals = {"__file__": str(context_temp_file)}

        if decorator_type == "with_key":

            @page_instance.context("user_name")
            def get_user_name():
                return "John Doe"

            func = get_user_name
        else:

            @page_instance.context
            def get_context_data():
                return {"key1": "value1", "key2": "value2"}

            func = get_context_data

        # verify context function was registered
        assert context_temp_file in page_instance._context_registry
        assert expected_key in page_instance._context_registry[context_temp_file]
        assert page_instance._context_registry[context_temp_file][expected_key] == func

    @pytest.fixture
    def test_file_path(self):
        """Create a test file path for render tests."""
        return Path("/test/path/page.py")

    @pytest.mark.parametrize(
        "test_case,template_str,context_setup,render_kwargs,expected",
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
                "Hello OverrideName! Count: 20",
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
    ):
        """Test various render scenarios with parametrized test cases."""
        # register template
        page_instance.register_template(test_file_path, template_str)

        # register context functions if any
        if context_setup:
            page_instance._context_registry[test_file_path] = context_setup

        # render
        result = page_instance.render(test_file_path, **render_kwargs)

        assert result == expected

    def test_render_with_multiple_files(self, page_instance):
        """Test render method with multiple files having different templates and contexts."""
        # first file
        file1 = Path("/test/path/page1.py")
        template1 = "Page 1: {{ title }}"
        page_instance.register_template(file1, template1)
        page_instance._context_registry[file1] = {"title": lambda: "First Page"}

        # second file
        file2 = Path("/test/path/page2.py")
        template2 = "Page 2: {{ title }}"
        page_instance.register_template(file2, template2)
        page_instance._context_registry[file2] = {"title": lambda: "Second Page"}

        # render both
        result1 = page_instance.render(file1)
        result2 = page_instance.render(file2)

        assert result1 == "Page 1: First Page"
        assert result2 == "Page 2: Second Page"

    def test_context_registry_defaultdict_behavior(self, page_instance, test_file_path):
        """Test that context registry uses defaultdict-like behavior."""
        # register context function - should create the file entry
        page_instance._context_registry.setdefault(test_file_path, {})["test_key"] = (
            lambda: "test_value"
        )

        assert test_file_path in page_instance._context_registry
        assert "test_key" in page_instance._context_registry[test_file_path]


class TestGlobalPageInstance:
    @pytest.fixture(autouse=True)
    def clear_global_state(self):
        """Clear global page state before each test."""
        page._template_registry.clear()
        page._context_registry.clear()
        yield
        page._template_registry.clear()
        page._context_registry.clear()

    @pytest.fixture
    def global_file_path(self):
        """Create a file path for global page tests."""
        return Path("/test/global/page.py")

    @pytest.fixture
    def mock_frame(self):
        """Mock inspect.currentframe for testing."""
        with patch("next.pages.inspect.currentframe") as mock_frame:
            yield mock_frame

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('template = "test template"')
            temp_file = Path(f.name)
        yield temp_file
        temp_file.unlink()

    @pytest.fixture
    def page_instance(self):
        """Create a fresh Page instance for each test."""
        return Page()

    def test_global_page_instance(self):
        """Test that global page instance is properly initialized."""
        assert page is not None
        assert isinstance(page, Page)
        assert page._template_registry == {}
        assert page._context_registry == {}

    def test_context_alias(self):
        """Test that context alias points to page.context."""
        assert context == page.context

    def test_global_page_template_registration(self, global_file_path):
        """Test template registration using global page instance."""
        template_str = "Global template: {{ message }}"
        page.register_template(global_file_path, template_str)

        assert global_file_path in page._template_registry
        assert page._template_registry[global_file_path] == template_str

    def test_global_page_context_registration(self, global_file_path, mock_frame):
        """Test context registration using global page instance."""
        mock_frame.return_value.f_back.f_globals = {"__file__": str(global_file_path)}

        @page.context("global_key")
        def get_global_value():
            return "global_value"

        assert global_file_path in page._context_registry
        assert "global_key" in page._context_registry[global_file_path]

    def test_global_page_render(self, global_file_path, mock_frame):
        """Test rendering using global page instance."""
        template_str = "Global: {{ key }}"
        page.register_template(global_file_path, template_str)

        mock_frame.return_value.f_back.f_globals = {"__file__": str(global_file_path)}

        @page.context("key")
        def get_key():
            return "value"

        result = page.render(global_file_path)
        assert result == "Global: value"

    def test_context_decorator_with_global_page(self, global_file_path, mock_frame):
        """Test context decorator with global page instance."""
        mock_frame.return_value.f_back.f_globals = {"__file__": str(global_file_path)}

        @context("test_key")
        def test_function():
            return "test_value"

        assert global_file_path in page._context_registry
        assert "test_key" in page._context_registry[global_file_path]
        assert page._context_registry[global_file_path]["test_key"] == test_function

    @pytest.mark.parametrize(
        "test_case,frame_setup",
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
                    mock_frame.return_value.f_back, "f_globals", {"__file__": None}
                ),
            ),
        ],
    )
    def test_get_caller_path_error_cases(self, page_instance, test_case, frame_setup):
        """Test _get_caller_path error cases with parametrized test cases."""
        with patch("next.pages.inspect.currentframe") as mock_frame:
            frame_setup(mock_frame)

            with pytest.raises(
                RuntimeError, match="Could not determine caller file path"
            ):
                page_instance._get_caller_path(1)
