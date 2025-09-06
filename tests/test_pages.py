import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from next.pages import (
    ContextManager,
    DjxTemplateLoader,
    Page,
    PythonTemplateLoader,
    context,
    page,
)
from next.urls import URLPatternParser


# common fixtures
@pytest.fixture
def page_instance():
    """Create a fresh Page instance for each test."""
    return Page()


@pytest.fixture
def url_parser():
    """Create a URLPatternParser instance for testing."""
    return URLPatternParser()


@pytest.fixture
def python_template_loader():
    """Create a PythonTemplateLoader instance for testing."""
    return PythonTemplateLoader()


@pytest.fixture
def djx_template_loader():
    """Create a DjxTemplateLoader instance for testing."""
    return DjxTemplateLoader()


@pytest.fixture
def context_manager():
    """Create a ContextManager instance for testing."""
    return ContextManager()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def temp_file():
    """Create a temporary file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write('template = "test template"')
        temp_file = Path(f.name)
    yield temp_file
    temp_file.unlink()


@pytest.fixture
def mock_frame():
    """Mock inspect.currentframe for testing."""
    with patch("next.pages.inspect.currentframe") as mock_frame:
        yield mock_frame


@pytest.fixture
def context_temp_file():
    """Create a temporary file for context decorator tests."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def test_func(): pass")
        temp_file = Path(f.name)
    yield temp_file
    temp_file.unlink()


@pytest.fixture
def test_file_path():
    """Create a test file path for render tests."""
    return Path("/test/path/page.py")


@pytest.fixture
def global_file_path():
    """Create a file path for global page tests."""
    return Path("/test/global/page.py")


class TestPage:
    def test_init(self, page_instance):
        """Test Page initialization."""
        assert page_instance._template_registry == {}
        assert isinstance(page_instance._context_manager, ContextManager)
        assert len(page_instance._template_loaders) == 2

    def test_register_template_direct(self, page_instance):
        """Test register_template method with direct file path."""
        file_path = Path("/test/path/page.py")
        template_str = "Hello {{ name }}!"

        page_instance.register_template(file_path, template_str)

        assert file_path in page_instance._template_registry
        assert page_instance._template_registry[file_path] == template_str

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
        assert context_temp_file in page_instance._context_manager._context_registry
        assert (
            expected_key
            in page_instance._context_manager._context_registry[context_temp_file]
        )
        assert (
            page_instance._context_manager._context_registry[context_temp_file][
                expected_key
            ]
            == func
        )

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
            for key, func in context_setup.items():
                page_instance._context_manager.register_context(
                    test_file_path, key, func
                )

        # render
        result = page_instance.render(test_file_path, **render_kwargs)

        assert result == expected

    def test_render_with_multiple_files(self, page_instance):
        """Test render method with multiple files having different templates and contexts."""
        # first file
        file1 = Path("/test/path/page1.py")
        template1 = "Page 1: {{ title }}"
        page_instance.register_template(file1, template1)
        page_instance._context_manager.register_context(
            file1, "title", lambda: "First Page"
        )

        # second file
        file2 = Path("/test/path/page2.py")
        template2 = "Page 2: {{ title }}"
        page_instance.register_template(file2, template2)
        page_instance._context_manager.register_context(
            file2, "title", lambda: "Second Page"
        )

        # render both
        result1 = page_instance.render(file1)
        result2 = page_instance.render(file2)

        assert result1 == "Page 1: First Page"
        assert result2 == "Page 2: Second Page"

    def test_context_registry_defaultdict_behavior(self, page_instance, test_file_path):
        """Test that context registry uses defaultdict-like behavior."""
        # register context function - should create the file entry
        page_instance._context_manager.register_context(
            test_file_path, "test_key", lambda: "test_value"
        )

        assert test_file_path in page_instance._context_manager._context_registry
        assert (
            "test_key"
            in page_instance._context_manager._context_registry[test_file_path]
        )


class TestGlobalPageInstance:
    @pytest.fixture(autouse=True)
    def clear_global_state(self):
        """Clear global page state before each test."""
        page._template_registry.clear()
        page._context_manager._context_registry.clear()
        yield
        page._template_registry.clear()
        page._context_manager._context_registry.clear()

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
        assert page._context_manager._context_registry == {}

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

        assert global_file_path in page._context_manager._context_registry
        assert "global_key" in page._context_manager._context_registry[global_file_path]

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

        assert global_file_path in page._context_manager._context_registry
        assert "test_key" in page._context_manager._context_registry[global_file_path]
        assert (
            page._context_manager._context_registry[global_file_path]["test_key"]
            == test_function
        )

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
            (
                "exhausted_frames",
                lambda mock_frame: setattr(
                    mock_frame.return_value.f_back, "f_back", None
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


class TestURLPatternParser:
    """Test cases for URL pattern parsing methods."""

    @pytest.mark.parametrize(
        "url_pattern,expected_pattern,expected_params",
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
    )
    def test_parse_url_pattern_variations(
        self, url_parser, url_pattern, expected_pattern, expected_params
    ):
        """Test parsing URL patterns with different variations."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)
        assert pattern == expected_pattern
        assert params == expected_params

    @pytest.mark.parametrize(
        "url_pattern,expected_contains",
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
        self, url_parser, url_pattern, expected_contains
    ):
        """Test parsing complex URL pattern."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)

        for expected in expected_contains:
            if expected.startswith("<"):
                assert expected in pattern
            else:
                assert expected in params

        assert pattern.endswith("/")  # should end with slash

    @pytest.mark.parametrize(
        "url_pattern,pattern_contains,params_condition",
        [
            ("[]", ["["] or ["<str:"], lambda p: len(p) == 0 or "" in p),
            ("[[]]", ["["] or ["<path:"], lambda p: len(p) == 0 or "" in p),
        ],
    )
    def test_parse_url_pattern_edge_cases(
        self, url_parser, url_pattern, pattern_contains, params_condition
    ):
        """Test parsing URL pattern edge cases."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)
        assert any(contains in pattern for contains in pattern_contains)
        assert params_condition(params)

    @pytest.mark.parametrize(
        "param_string,expected_name,expected_type",
        [
            ("param", "param", "str"),
            ("int:user-id", "user-id", "int"),
            ("", "", "str"),
            ("   ", "", "str"),
            (":param", "param", ""),
        ],
    )
    def test_parse_param_name_and_type_variations(
        self, url_parser, param_string, expected_name, expected_type
    ):
        """Test parsing parameter name and type with different variations."""
        name, type_name = url_parser._parse_param_name_and_type(param_string)
        assert name == expected_name
        assert type_name == expected_type

    @pytest.mark.parametrize(
        "url_path,expected_params,expected_pattern",
        [
            (
                "user/[[profile]]/[int:user-id]/posts",
                ["profile", "user_id"],
                "user/<path:profile>/<int:user_id>/posts/",
            ),
        ],
    )
    def test_parse_url_pattern_with_args_and_params(
        self, url_parser, url_path, expected_params, expected_pattern
    ):
        """Test parsing URL pattern with both args and regular parameters."""
        django_pattern, parameters = url_parser.parse_url_pattern(url_path)

        for param in expected_params:
            assert param in parameters
        assert django_pattern == expected_pattern

    @pytest.mark.parametrize(
        "url_path,expected_name",
        [
            ("user/[int:user-id]/posts", "user_int_user_id_posts"),
            ("profile/[[args]]", "profile_args"),
            (
                "user/[int:id]/posts/[slug:post-slug]/[[args]]",
                "user_int_id_posts_slug_post_slug_args",
            ),
        ],
    )
    def test_prepare_url_name_with_colons(self, url_parser, url_path, expected_name):
        """Test URL name preparation with colons in parameter syntax."""
        clean_name = url_parser.prepare_url_name(url_path)
        assert clean_name == expected_name
        assert ":" not in clean_name


class TestPythonTemplateLoader:
    """Test cases for Python template loader."""

    def test_can_load_with_template_attribute(self, python_template_loader, temp_dir):
        """Test can_load when module has template attribute."""
        # create page.py file with template attribute
        page_file = temp_dir / "page.py"
        page_file.write_text('template = "Hello {{ name }}!"')

        result = python_template_loader.can_load(page_file)
        assert result is True

    def test_can_load_without_template_attribute(
        self, python_template_loader, temp_dir
    ):
        """Test can_load when module doesn't have template attribute."""
        # create page.py file without template attribute
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        result = python_template_loader.can_load(page_file)
        assert result is False

    def test_can_load_invalid_file(self, python_template_loader, temp_dir):
        """Test can_load with invalid Python file."""
        # create invalid Python file
        page_file = temp_dir / "page.py"
        page_file.write_text("invalid python syntax !!!")

        result = python_template_loader.can_load(page_file)
        assert result is False

    def test_load_template_success(self, python_template_loader, temp_dir):
        """Test successful template loading."""
        # create page.py file with template attribute
        page_file = temp_dir / "page.py"
        template_content = "Hello {{ name }}!"
        page_file.write_text(f'template = "{template_content}"')

        result = python_template_loader.load_template(page_file)
        assert result == template_content

    def test_load_template_no_template_attribute(
        self, python_template_loader, temp_dir
    ):
        """Test loading when module has no template attribute."""
        # create page.py file without template attribute
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        result = python_template_loader.load_template(page_file)
        assert result is None

    def test_load_template_invalid_file(self, python_template_loader, temp_dir):
        """Test loading with invalid Python file."""
        # create invalid Python file
        page_file = temp_dir / "page.py"
        page_file.write_text("invalid python syntax !!!")

        result = python_template_loader.load_template(page_file)
        assert result is None

    @patch("importlib.util.spec_from_file_location")
    def test_can_load_spec_none(self, mock_spec, python_template_loader, temp_dir):
        """Test can_load when spec is None."""
        mock_spec.return_value = None

        page_file = temp_dir / "page.py"
        page_file.write_text('template = "test"')

        result = python_template_loader.can_load(page_file)
        assert result is False

    @patch("importlib.util.spec_from_file_location")
    def test_can_load_loader_none(self, mock_spec, python_template_loader, temp_dir):
        """Test can_load when spec.loader is None."""
        mock_spec_obj = mock_spec.return_value
        mock_spec_obj.loader = None

        page_file = temp_dir / "page.py"
        page_file.write_text('template = "test"')

        result = python_template_loader.can_load(page_file)
        assert result is False

    @patch("importlib.util.spec_from_file_location")
    def test_load_template_spec_none(self, mock_spec, python_template_loader, temp_dir):
        """Test load_template when spec is None."""
        mock_spec.return_value = None

        page_file = temp_dir / "page.py"
        page_file.write_text('template = "test"')

        result = python_template_loader.load_template(page_file)
        assert result is None

    @patch("importlib.util.spec_from_file_location")
    def test_load_template_loader_none(
        self, mock_spec, python_template_loader, temp_dir
    ):
        """Test load_template when spec.loader is None."""
        mock_spec_obj = mock_spec.return_value
        mock_spec_obj.loader = None

        page_file = temp_dir / "page.py"
        page_file.write_text('template = "test"')

        result = python_template_loader.load_template(page_file)
        assert result is None


class TestContextManager:
    """Test cases for ContextManager."""

    def test_init(self, context_manager):
        """Test ContextManager initialization."""
        assert context_manager._context_registry == {}

    def test_register_context_with_key(self, context_manager, test_file_path):
        """Test registering context function with key."""

        def test_func():
            return "test_value"

        context_manager.register_context(test_file_path, "test_key", test_func)

        assert test_file_path in context_manager._context_registry
        assert "test_key" in context_manager._context_registry[test_file_path]
        assert (
            context_manager._context_registry[test_file_path]["test_key"] == test_func
        )

    def test_register_context_without_key(self, context_manager, test_file_path):
        """Test registering context function without key."""

        def test_func():
            return {"key1": "value1", "key2": "value2"}

        context_manager.register_context(test_file_path, None, test_func)

        assert test_file_path in context_manager._context_registry
        assert None in context_manager._context_registry[test_file_path]
        assert context_manager._context_registry[test_file_path][None] == test_func

    def test_collect_context_with_key(self, context_manager, test_file_path):
        """Test collecting context with key."""

        def test_func():
            return "test_value"

        context_manager.register_context(test_file_path, "test_key", test_func)

        result = context_manager.collect_context(test_file_path)

        assert result == {"test_key": "test_value"}

    def test_collect_context_without_key(self, context_manager, test_file_path):
        """Test collecting context without key."""

        def test_func():
            return {"key1": "value1", "key2": "value2"}

        context_manager.register_context(test_file_path, None, test_func)

        result = context_manager.collect_context(test_file_path)

        assert result == {"key1": "value1", "key2": "value2"}

    def test_collect_context_multiple_functions(self, context_manager, test_file_path):
        """Test collecting context with multiple functions."""

        def func1():
            return "value1"

        def func2():
            return {"key2": "value2", "key3": "value3"}

        context_manager.register_context(test_file_path, "key1", func1)
        context_manager.register_context(test_file_path, None, func2)

        result = context_manager.collect_context(test_file_path)

        assert result == {"key1": "value1", "key2": "value2", "key3": "value3"}

    def test_collect_context_no_functions(self, context_manager, test_file_path):
        """Test collecting context when no functions are registered."""
        result = context_manager.collect_context(test_file_path)

        assert result == {}


class TestDjxTemplateLoader:
    """Test cases for DJX template loader."""

    def test_load_djx_template_success(self, djx_template_loader, temp_dir):
        """Test successful loading of template.djx template."""
        # create page.py file
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file
        djx_file = temp_dir / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>{{ content }}</p>"
        djx_file.write_text(djx_content)

        # test loading
        result = djx_template_loader.load_template(page_file)

        assert result == djx_content

    def test_load_djx_template_file_not_exists(self, djx_template_loader, temp_dir):
        """Test loading when template.djx file doesn't exist."""
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        result = djx_template_loader.load_template(page_file)

        assert result is None

    def test_load_djx_template_encoding_error(self, djx_template_loader, temp_dir):
        """Test loading with encoding error."""
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file with invalid encoding
        djx_file = temp_dir / "template.djx"
        djx_file.write_bytes(b"\xff\xfe\x00\x00")  # invalid utf-8

        result = djx_template_loader.load_template(page_file)

        assert result is None

    def test_create_url_pattern_with_djx_template(self, page_instance, temp_dir):
        """Test create_url_pattern with template.djx template."""
        # create page.py file without template attribute
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file
        djx_file = temp_dir / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>"
        djx_file.write_text(djx_content)

        # test create_url_pattern
        url_parser = URLPatternParser()
        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        assert page_file in page_instance._template_registry
        assert page_instance._template_registry[page_file] == djx_content

    def test_create_url_pattern_priority_template_over_djx(
        self, page_instance, temp_dir
    ):
        """Test that template attribute takes priority over template.djx file."""
        # create page.py file with template attribute
        page_file = temp_dir / "page.py"
        page_file.write_text('template = "Python template: {{ name }}"')

        # create template.djx file
        djx_file = temp_dir / "template.djx"
        djx_content = "<h1>DJX template: {{ name }}</h1>"
        djx_file.write_text(djx_content)

        # test create_url_pattern
        url_parser = URLPatternParser()
        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        assert (
            page_instance._template_registry[page_file] == "Python template: {{ name }}"
        )

    def test_render_djx_template_with_context(self, page_instance, temp_dir):
        """Test rendering template.djx template with context."""
        # create page.py file
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file
        djx_file = temp_dir / "template.djx"
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

    def test_render_djx_template_with_django_tags(self, page_instance, temp_dir):
        """Test rendering template.djx template with Django tags."""
        # create page.py file
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file with Django tags
        djx_file = temp_dir / "template.djx"
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
            page_file, title="Items", items=["Apple", "Banana"]
        )

        assert "Items" in result
        assert "Apple" in result
        assert "Banana" in result
        assert "<li>" in result

    def test_djx_template_with_context_functions(self, page_instance, temp_dir):
        """Test template.djx template with context functions."""
        # create page.py file with context function
        page_file = temp_dir / "page.py"
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
        djx_file = temp_dir / "template.djx"
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


class TestPageChecks:
    """Test cases for page checks functionality."""

    def test_has_template_or_djx_with_template(self, temp_dir):
        """Test _has_template_or_djx with template attribute."""
        from next.checks import _has_template_or_djx

        # create page.py file with template
        page_file = temp_dir / "page.py"
        page_file.write_text('template = "Hello {{ name }}!"')

        result = _has_template_or_djx(page_file)
        assert result is True

    def test_has_template_or_djx_with_djx_file(self, temp_dir):
        """Test _has_template_or_djx with template.djx file."""
        from next.checks import _has_template_or_djx

        # create page.py file without template
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file
        djx_file = temp_dir / "template.djx"
        djx_file.write_text("<h1>{{ title }}</h1>")

        result = _has_template_or_djx(page_file)
        assert result is True

    def test_has_template_or_djx_without_template_or_djx(self, temp_dir):
        """Test _has_template_or_djx without template or template.djx."""
        from next.checks import _has_template_or_djx

        # create page.py file without template
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        result = _has_template_or_djx(page_file)
        assert result is False

    def test_has_template_or_djx_with_render_function(self, temp_dir):
        """Test _has_template_or_djx with render function but no template."""
        from next.checks import _has_template_or_djx

        # create page.py file with render function
        page_file = temp_dir / "page.py"
        page_file.write_text("""
def render(request, **kwargs):
    return "Hello World!"
        """)

        result = _has_template_or_djx(page_file)
        assert result is False  # render function doesn't count as template

    def test_has_template_or_djx_invalid_file(self, temp_dir):
        """Test _has_template_or_djx with invalid file."""
        from next.checks import _has_template_or_djx

        # create invalid page.py file
        page_file = temp_dir / "page.py"
        page_file.write_text("invalid python syntax {")

        result = _has_template_or_djx(page_file)
        assert result is False
