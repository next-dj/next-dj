import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from next.pages import (
    ContextManager,
    DjxTemplateLoader,
    LayoutManager,
    LayoutTemplateLoader,
    Page,
    PythonTemplateLoader,
    context,
    page,
)
from next.urls import URLPatternParser


# shared fixtures
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
def layout_manager():
    """Create a LayoutManager instance for testing."""
    return LayoutManager()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def mock_frame():
    """Mock inspect.currentframe for testing."""
    with patch("next.pages.inspect.currentframe") as mock_frame:
        yield mock_frame


@pytest.fixture
def test_file_path():
    """Create a test file path for render tests."""
    return Path("/test/path/page.py")


@pytest.fixture
def global_file_path():
    """Create a file path for global page tests."""
    return Path("/test/global/page.py")


@pytest.fixture
def temp_python_file():
    """Create a temporary Python file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write('template = "test template"')
        temp_file = Path(f.name)
    yield temp_file
    temp_file.unlink()


@pytest.fixture
def context_temp_file():
    """Create a temporary file for context decorator tests."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def test_func(): pass")
        temp_file = Path(f.name)
    yield temp_file
    temp_file.unlink()


class TestPage:
    def test_init(self, page_instance):
        """Test Page initialization."""
        assert page_instance._template_registry == {}
        assert isinstance(page_instance._context_manager, ContextManager)
        assert len(page_instance._template_loaders) == 3
        # check that all expected loaders are present
        loader_types = [type(loader) for loader in page_instance._template_loaders]
        assert PythonTemplateLoader in loader_types
        assert DjxTemplateLoader in loader_types
        assert LayoutTemplateLoader in loader_types

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

    def test_scan_pages_directory_virtual_view_detection(self, temp_dir):
        """Test _scan_pages_directory detects virtual views (template.djx without page.py)."""
        from next.urls import FileRouterBackend

        # create a FileRouterBackend instance
        backend = FileRouterBackend()

        # create a directory structure with template.djx but no page.py
        virtual_dir = temp_dir / "virtual"
        virtual_dir.mkdir()
        template_file = virtual_dir / "template.djx"
        template_file.write_text("<h1>Virtual Page</h1>")

        # scan the directory using the instance method
        results = list(backend._scan_pages_directory(temp_dir))

        # should find the virtual page
        assert len(results) == 1
        url_path, page_path = results[0]
        assert url_path == "virtual"
        assert page_path == virtual_dir / "page.py"  # virtual page.py path


class TestPythonTemplateLoader:
    """Test cases for Python template loader."""

    @pytest.mark.parametrize(
        "file_content,expected_can_load,expected_load_result",
        [
            ('template = "Hello {{ name }}!"', True, "Hello {{ name }}!"),
            ('print("test")', False, None),
            ("invalid python syntax !!!", False, None),
        ],
    )
    def test_can_load_and_load_template(
        self,
        python_template_loader,
        temp_dir,
        file_content,
        expected_can_load,
        expected_load_result,
    ):
        """Test can_load and load_template with different file contents."""
        page_file = temp_dir / "page.py"
        page_file.write_text(file_content)

        can_load_result = python_template_loader.can_load(page_file)
        load_result = python_template_loader.load_template(page_file)

        assert can_load_result is expected_can_load
        assert load_result == expected_load_result


class TestContextManager:
    """Test cases for ContextManager."""

    def test_init(self, context_manager):
        """Test ContextManager initialization."""
        assert context_manager._context_registry == {}

    @pytest.mark.parametrize(
        "key,func_return,expected_result",
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
        self, context_manager, test_file_path, key, func_return, expected_result
    ):
        """Test registering and collecting context with different key types."""
        context_manager.register_context(test_file_path, key, func_return)

        assert test_file_path in context_manager._context_registry
        assert key in context_manager._context_registry[test_file_path]
        assert context_manager._context_registry[test_file_path][key] == func_return

        result = context_manager.collect_context(test_file_path)
        assert result == expected_result

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

    @pytest.mark.parametrize(
        "create_djx_file,djx_content,expected_result",
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
        temp_dir,
        create_djx_file,
        djx_content,
        expected_result,
    ):
        """Test loading of template.djx template with different scenarios."""
        # create page.py file
        page_file = temp_dir / "page.py"
        page_file.write_text('print("test")')

        # create template.djx file if needed
        if create_djx_file:
            djx_file = temp_dir / "template.djx"
            djx_file.write_text(djx_content)

        # test loading
        result = djx_template_loader.load_template(page_file)

        assert result == expected_result

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


class TestCreateUrlPatternScenarios:
    """Test cases for different URL pattern creation scenarios."""

    @pytest.mark.parametrize(
        "test_case,page_content,create_djx,djx_content,url_pattern,expected_pattern_name,expected_template",
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
        temp_dir,
        url_parser,
        test_case,
        page_content,
        create_djx,
        djx_content,
        url_pattern,
        expected_pattern_name,
        expected_template,
    ):
        """Test various create_url_pattern scenarios."""
        page_file = temp_dir / "page.py"

        # create page.py file if content provided
        if page_content:
            page_file.write_text(page_content)

        # create template.djx file if needed
        if create_djx:
            djx_file = temp_dir / "template.djx"
            djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern(url_pattern, page_file, url_parser)

        if expected_pattern_name:
            assert pattern is not None
            assert pattern.name == expected_pattern_name
            if expected_template:
                assert page_file in page_instance._template_registry
                assert page_instance._template_registry[page_file] == expected_template
        else:
            assert pattern is None

    def test_create_url_pattern_render_function_fallback(
        self, page_instance, temp_dir, url_parser
    ):
        """Test that render function is used as fallback when no template is found."""
        page_file = temp_dir / "page.py"
        page_file.write_text("""
from django.http import HttpResponse

def render(request, **kwargs):
    return HttpResponse("Fallback render function!")
        """)

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        assert pattern.name == "page_test"

    def test_create_url_pattern_virtual_view_rendering(
        self, page_instance, temp_dir, url_parser
    ):
        """Test that virtual view can be rendered with context."""
        page_file = temp_dir / "page.py"
        djx_file = temp_dir / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>"
        djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None

        # test rendering
        result = page_instance.render(page_file, title="Welcome", name="World")
        assert result == "<h1>Welcome</h1><p>Hello World!</p>"

    def test_create_url_pattern_with_context_functions(
        self, page_instance, temp_dir, url_parser
    ):
        """Test create_url_pattern with context functions for virtual view."""
        page_file = temp_dir / "page.py"
        djx_file = temp_dir / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>{{ description }}</p>"
        djx_file.write_text(djx_content)

        # register context function
        page_instance._context_manager.register_context(
            page_file, "title", lambda: "Context Title"
        )
        page_instance._context_manager.register_context(
            page_file, "description", lambda: "Context Description"
        )

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None

        # test rendering with context
        result = page_instance.render(page_file)
        assert result == "<h1>Context Title</h1><p>Context Description</p>"


class TestPageChecks:
    """Test cases for page checks functionality."""

    @pytest.mark.parametrize(
        "page_content,create_djx,djx_content,expected_result",
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
        self, temp_dir, page_content, create_djx, djx_content, expected_result
    ):
        """Test _has_template_or_djx with different scenarios."""
        from next.checks import _has_template_or_djx

        page_file = temp_dir / "page.py"
        page_file.write_text(page_content)

        if create_djx:
            djx_file = temp_dir / "template.djx"
            djx_file.write_text(djx_content)

        result = _has_template_or_djx(page_file)
        assert result is expected_result


class TestLayoutTemplateLoader:
    """Test cases for LayoutTemplateLoader."""

    @pytest.mark.parametrize(
        "create_layout,create_template,expected_can_load",
        [
            (True, True, True),
            (False, True, False),
            (True, False, True),
            (False, False, False),
        ],
    )
    def test_can_load_with_layout_files(
        self, temp_dir, create_layout, create_template, expected_can_load
    ):
        """Test can_load with different layout and template combinations."""
        loader = LayoutTemplateLoader()

        # create nested directory structure
        sub_dir = temp_dir / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        # create layout.djx in parent directory if needed
        if create_layout:
            layout_file = temp_dir / "layout.djx"
            layout_file.write_text(
                "<html><body>{% block template %}{% endblock template %}</body></html>"
            )

        # create template.djx in nested directory if needed
        if create_template:
            template_file = sub_dir / "template.djx"
            template_file.write_text("<h1>Test Content</h1>")

        # test with page.py path in nested directory
        page_file = sub_dir / "page.py"

        result = loader.can_load(page_file)
        assert result is expected_can_load

    def test_load_template_with_single_layout(self, temp_dir):
        """Test load_template with single layout file."""
        loader = LayoutTemplateLoader()

        # create layout.djx
        layout_file = temp_dir / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        # create template.djx
        sub_dir = temp_dir / "sub"
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

    def test_load_template_with_multiple_layouts(self, temp_dir):
        """Test load_template with multiple layout files in hierarchy."""
        loader = LayoutTemplateLoader()

        # create root layout
        root_layout = temp_dir / "layout.djx"
        root_layout.write_text(
            "<html><head><title>Root</title></head><body>{% block template %}{% endblock template %}</body></html>"
        )

        # create sub layout
        sub_dir = temp_dir / "sub"
        sub_dir.mkdir()
        sub_layout = sub_dir / "layout.djx"
        sub_layout.write_text(
            "<div class='sub-layout'>{% block template %}{% endblock template %}</div>"
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

    def test_load_template_without_template_djx(self, temp_dir):
        """Test load_template when template.djx doesn't exist."""
        loader = LayoutTemplateLoader()

        # create layout.djx
        layout_file = temp_dir / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        # create page.py without template.djx
        page_file = temp_dir / "page.py"

        result = loader.load_template(page_file)

        assert result is not None
        # should contain layout content
        assert "<html><body>" in result
        assert "</body></html>" in result
        # should contain empty template block
        assert "{% block template %}{% endblock template %}" in result

    def test_find_layout_files(self, temp_dir):
        """Test _find_layout_files method."""
        loader = LayoutTemplateLoader()

        # create nested structure with layouts
        sub_dir = temp_dir / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        # create layouts at different levels
        root_layout = temp_dir / "layout.djx"
        root_layout.write_text("root layout")

        sub_layout = temp_dir / "sub" / "layout.djx"
        sub_layout.write_text("sub layout")

        page_file = sub_dir / "page.py"
        layout_files = loader._find_layout_files(page_file)

        assert layout_files is not None
        assert len(layout_files) == 2
        assert sub_layout in layout_files  # closest first
        assert root_layout in layout_files

    def test_compose_layout_hierarchy_exception_handling(self, temp_dir):
        """Test _compose_layout_hierarchy handles exceptions gracefully."""
        from unittest.mock import patch

        loader = LayoutTemplateLoader()

        # create a layout file that will cause an exception when read
        layout_file = temp_dir / "layout.djx"
        layout_file.write_text("test")

        # create template file
        template_file = temp_dir / "template.djx"
        template_file.write_text("test")

        # mock the read_text method to raise an exception
        with patch("pathlib.Path.read_text", side_effect=OSError("Mocked error")):
            # test that exception is handled gracefully
            result = loader._compose_layout_hierarchy("test content", [layout_file])
            assert (
                result == "test content"
            )  # should return original content when exception occurs

    def test_load_template_no_layout_files(self, temp_dir):
        """Test load_template when no layout files exist."""
        loader = LayoutTemplateLoader()

        # create a page file without layout files
        page_file = temp_dir / "page.py"
        page_file.write_text("template = 'test'")

        result = loader.load_template(page_file)
        assert result is None

    def test_wrap_in_template_block_no_template_file(self, temp_dir):
        """Test _wrap_in_template_block when template.djx doesn't exist."""
        loader = LayoutTemplateLoader()

        # create a page file without template.djx
        page_file = temp_dir / "page.py"
        page_file.write_text("template = 'test'")

        result = loader._wrap_in_template_block(page_file)
        assert result == "{% block template %}{% endblock template %}"


class TestLayoutManager:
    """Test cases for LayoutManager."""

    def test_init(self):
        """Test LayoutManager initialization."""
        manager = LayoutManager()
        assert manager._layout_registry == {}
        assert isinstance(manager._layout_loader, LayoutTemplateLoader)

    def test_discover_layouts_for_template(self, temp_dir):
        """Test discover_layouts_for_template method."""
        manager = LayoutManager()

        # create layout structure
        layout_file = temp_dir / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        sub_dir = temp_dir / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>Test</h1>")

        page_file = sub_dir / "page.py"
        result = manager.discover_layouts_for_template(page_file)

        assert result is not None
        assert page_file in manager._layout_registry

    def test_discover_layouts_no_layouts(self, temp_dir):
        """Test discover_layouts_for_template when no layouts exist."""
        manager = LayoutManager()

        sub_dir = temp_dir / "sub"
        sub_dir.mkdir()
        page_file = sub_dir / "page.py"

        result = manager.discover_layouts_for_template(page_file)

        assert result is None
        assert page_file not in manager._layout_registry

    def test_get_layout_template(self, temp_dir):
        """Test get_layout_template method."""
        manager = LayoutManager()

        # create layout structure
        layout_file = temp_dir / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        sub_dir = temp_dir / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>Test</h1>")

        page_file = sub_dir / "page.py"
        manager.discover_layouts_for_template(page_file)

        result = manager.get_layout_template(page_file)
        assert result is not None

    def test_get_layout_template_not_found(self, temp_dir):
        """Test get_layout_template when template not found."""
        manager = LayoutManager()

        page_file = temp_dir / "page.py"
        result = manager.get_layout_template(page_file)

        assert result is None

    def test_clear_registry(self):
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

    def test_page_with_layout_manager(self, page_instance):
        """Test that Page class has LayoutManager."""
        assert hasattr(page_instance, "_layout_manager")
        assert isinstance(page_instance._layout_manager, LayoutManager)

    def test_create_url_pattern_with_layout(self, page_instance, temp_dir, url_parser):
        """Test create_url_pattern with layout inheritance."""
        # create layout structure
        layout_file = temp_dir / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        # create template.djx
        sub_dir = temp_dir / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>{{ title }}</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"
        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        assert page_file in page_instance._template_registry

    def test_render_with_layout_inheritance(self, page_instance, temp_dir):
        """Test rendering with layout inheritance."""
        # create layout structure
        layout_file = temp_dir / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        # create template.djx
        sub_dir = temp_dir / "sub"
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

    def test_load_template_for_file_layout_fallback(self, page_instance, temp_dir):
        """Test _load_template_for_file with layout fallback."""
        # create layout structure
        layout_file = temp_dir / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        # create template.djx
        sub_dir = temp_dir / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance._load_template_for_file(page_file)

        assert result is True
        assert page_file in page_instance._template_registry

    def test_render_with_layout_template_detection(self, page_instance, temp_dir):
        """Test render method with layout template detection."""
        # create a template that looks like a layout template but doesn't use extends
        page_file = temp_dir / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        result = page_instance.render(page_file, title="Test")

        # should use regular template rendering
        assert result == "<h1>Test</h1>"


class TestContextProcessors:
    """Test context_processors functionality."""

    def test_get_context_processors_empty_config(self, page_instance):
        """Test _get_context_processors with empty NEXT_PAGES config."""
        from next.pages import _get_context_processors

        with patch("django.conf.settings.NEXT_PAGES", [], create=True):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_no_context_processors(self, page_instance):
        """Test _get_context_processors with NEXT_PAGES config but no context_processors."""
        from next.pages import _get_context_processors

        config = [{"BACKEND": "next.urls.FileRouterBackend", "OPTIONS": {}}]
        with patch("django.conf.settings.NEXT_PAGES", config, create=True):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_with_valid_processors(self, page_instance):
        """Test _get_context_processors with valid context processors."""
        from next.pages import _get_context_processors

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
                        ]
                    },
                }
            ]

            with patch("django.conf.settings.NEXT_PAGES", config, create=True):
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == test_processor
                assert processors[1] == another_processor

    def test_get_context_processors_with_invalid_processor(self, page_instance):
        """Test _get_context_processors with invalid processor path."""
        from next.pages import _get_context_processors

        config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "OPTIONS": {
                    "context_processors": [
                        "invalid.module.path",
                        "django.template.context_processors.request",
                    ]
                },
            }
        ]

        with patch("django.conf.settings.NEXT_PAGES", config, create=True):
            with patch("next.pages.import_string") as mock_import:
                mock_import.side_effect = [
                    ImportError("No module named 'invalid'"),
                    lambda request: {"request": request},
                ]

                with patch("logging.getLogger") as mock_logger:
                    processors = _get_context_processors()
                    assert len(processors) == 1
                    # check that warning was logged
                    mock_logger.return_value.warning.assert_called_once()

    def test_render_with_context_processors(self, page_instance, temp_dir):
        """Test render method with context_processors."""

        # create a test template
        page_file = temp_dir / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ request_var }}</p>"
        page_instance.register_template(page_file, template_str)

        # create a mock request that is an instance of HttpRequest
        from django.http import HttpRequest

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

    def test_render_without_request_object(self, page_instance, temp_dir):
        """Test render method without request object (should use regular Context)."""

        # create a test template
        page_file = temp_dir / "page.py"
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

    def test_render_without_context_processors(self, page_instance, temp_dir):
        """Test render method without context_processors (should use regular Context)."""
        # create a test template
        page_file = temp_dir / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        # create a mock request that is an instance of HttpRequest
        from django.http import HttpRequest

        mock_request = HttpRequest()
        mock_request.META = {}

        # mock _get_context_processors to return empty list
        with patch("next.pages._get_context_processors", return_value=[]):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            # should use regular Context, not RequestContext
            assert result == "<h1>Test Title</h1>"

    def test_render_with_context_processor_error(self, page_instance, temp_dir):
        """Test render method with context processor that raises an exception."""
        from django.http import HttpRequest

        # create a test template
        page_file = temp_dir / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ good_var }}</p>"
        page_instance.register_template(page_file, template_str)

        # create a mock request
        mock_request = HttpRequest()
        mock_request.META = {}

        def error_processor(request):
            raise ValueError("Test error")

        def good_processor(request):
            return {"good_var": "good_value"}

        # mock _get_context_processors to return processors with one that errors
        with patch(
            "next.pages._get_context_processors",
            return_value=[error_processor, good_processor],
        ):
            with patch("next.pages.logger") as mock_logger:
                result = page_instance.render(
                    page_file, mock_request, title="Test Title"
                )

                # should include good processor data but not error_var
                assert "Test Title" in result
                assert "good_value" in result
                # check that error was logged
                mock_logger.warning.assert_called_once()

    def test_render_with_context_processor_non_dict_return(
        self, page_instance, temp_dir
    ):
        """Test render method with context processor that returns non-dict."""
        from django.http import HttpRequest

        # create a test template
        page_file = temp_dir / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ good_var }}</p>"
        page_instance.register_template(page_file, template_str)

        # create a mock request
        mock_request = HttpRequest()
        mock_request.META = {}

        def non_dict_processor(request):
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

    def test_load_python_module_invalid_file(self, temp_dir):
        """Test _load_python_module with invalid Python file."""
        from next.pages import _load_python_module

        # create an invalid Python file
        invalid_file = temp_dir / "invalid.py"
        invalid_file.write_text("invalid python syntax {")

        result = _load_python_module(invalid_file)
        assert result is None

    def test_load_python_module_nonexistent_file(self, temp_dir):
        """Test _load_python_module with nonexistent file."""
        from next.pages import _load_python_module

        nonexistent_file = temp_dir / "nonexistent.py"

        result = _load_python_module(nonexistent_file)
        assert result is None


class TestPageCreateUrlPattern:
    """Test Page create_url_pattern functionality."""

    def test_create_regular_page_pattern_no_module(self, page_instance, temp_dir):
        """Test _create_regular_page_pattern when module cannot be loaded."""
        from next.urls import URLPatternParser

        # create an invalid page file
        page_file = temp_dir / "page.py"
        page_file.write_text("invalid python syntax {")

        url_parser = URLPatternParser()
        django_pattern, parameters = url_parser.parse_url_pattern("test")
        clean_name = url_parser.prepare_url_name("test")

        result = page_instance._create_regular_page_pattern(
            page_file, django_pattern, parameters, clean_name
        )
        assert result is None

    def test_create_regular_page_pattern_no_template_no_render(
        self, page_instance, temp_dir
    ):
        """Test _create_regular_page_pattern when no template and no render function."""
        from next.urls import URLPatternParser

        # create a page file without template or render function
        page_file = temp_dir / "page.py"
        page_file.write_text("def other_function(): pass")

        url_parser = URLPatternParser()
        django_pattern, parameters = url_parser.parse_url_pattern("test")
        clean_name = url_parser.prepare_url_name("test")

        result = page_instance._create_regular_page_pattern(
            page_file, django_pattern, parameters, clean_name
        )
        assert result is None
