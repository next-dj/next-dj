"""Tests for URL pattern parsing and page URL pattern creation."""

import pytest

from next.urls import FileRouterBackend, URLPatternParser


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
        ids=["complex_pattern"],
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

        assert pattern.endswith("/")

    @pytest.mark.parametrize(
        ("url_pattern", "pattern_contains", "params_condition"),
        [
            ("[]", ["["], lambda p: len(p) == 0 or "" in p),
            ("[[]]", ["["], lambda p: len(p) == 0 or "" in p),
        ],
        ids=["empty_bracket", "empty_double_bracket"],
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
        ids=["simple_param", "typed_param", "empty", "whitespace", "colon_prefix"],
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
        ids=["args_and_params"],
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
        ids=["int_param", "path_param", "mixed_params"],
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
        backend = FileRouterBackend()

        virtual_dir = tmp_path / "virtual"
        virtual_dir.mkdir()
        template_file = virtual_dir / "template.djx"
        template_file.write_text("<h1>Virtual Page</h1>")

        results = list(backend._scan_pages_directory(tmp_path))

        assert len(results) == 1
        url_path, page_path = results[0]
        assert url_path == "virtual"
        assert page_path == virtual_dir / "page.py"


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
        ids=[
            "render_function_only",
            "template_priority",
            "virtual_view_djx",
            "virtual_view_no_djx",
            "virtual_view_with_params",
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

        if page_content:
            page_file.write_text(page_content)

        if create_djx:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern(url_pattern, page_file, url_parser)

        if expected_pattern_name:
            assert pattern is not None
            assert pattern.name == expected_pattern_name
            if expected_template:
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

        result = page_instance.render(page_file)
        assert result == "<h1>Context Title</h1><p>Context Description</p>"


class TestPageCreateUrlPattern:
    """Test Page create_url_pattern functionality."""

    def test_create_regular_page_pattern_no_module(
        self, page_instance, tmp_path
    ) -> None:
        """Test _create_regular_page_pattern when module cannot be loaded."""
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
