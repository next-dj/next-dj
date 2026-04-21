"""Tests for next.pages check functions."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from next.checks import (
    _has_template_or_djx,
    check_context_functions,
    check_duplicate_url_parameters,
    check_layout_templates,
    check_page_functions,
)
from tests.support import (
    patch_checks_router_manager,
    patch_checks_router_manager_with_routers,
)


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
        ids=[
            "template_attr",
            "with_djx",
            "no_template_no_djx",
            "render_function_only",
            "invalid_syntax",
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

    @pytest.mark.parametrize(
        ("layout_body", "expected_warnings", "msg_substring"),
        [
            (
                "<html>{% block template %}{% endblock template %}</html>",
                0,
                None,
            ),
            (
                "<html><body>No template block</body></html>",
                1,
                "does not contain required {% block template %}",
            ),
        ],
        ids=["with_block", "without_block"],
    )
    def test_check_layout_templates_scenarios(
        self,
        tmp_path,
        layout_body,
        expected_warnings,
        msg_substring,
    ) -> None:
        """Layout.djx with or without required ``{% block template %}``."""
        (tmp_path / "layout.djx").write_text(layout_body)
        page_file = tmp_path / "page.py"
        page_file.write_text("")

        with patch_checks_router_manager(
            pages_directory=tmp_path,
            scan_routes=[("test", page_file)],
        ):
            warnings = check_layout_templates(None)
        assert len(warnings) == expected_warnings
        if msg_substring is not None:
            assert msg_substring in warnings[0].msg


class TestMissingPageContentChecks:
    """Merged page checks: ``check_page_functions`` covers template/render and W002."""

    @pytest.mark.parametrize(
        (
            "test_case",
            "page_content",
            "create_template_djx",
            "template_djx_content",
            "create_layout_djx",
            "layout_djx_content",
            "expected_errors",
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
                0,
            ),
            (
                "with_layout_djx",
                "",
                False,
                None,
                True,
                "<html>{% block template %}{% endblock template %}</html>",
                1,
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
                0,
            ),
        ],
        ids=[
            "with_template",
            "with_render",
            "with_template_djx",
            "with_layout_djx",
            "no_content",
        ],
    )
    def test_check_page_functions_content_scenarios(
        self,
        tmp_path,
        test_case,
        page_content,
        create_template_djx,
        template_djx_content,
        create_layout_djx,
        layout_djx_content,
        expected_errors,
        expected_warnings,
    ) -> None:
        """Exercise ``check_page_functions`` for template/render rules and empty pages."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)

        if create_template_djx:
            template_djx = tmp_path / "template.djx"
            template_djx.write_text(template_djx_content)

        if create_layout_djx:
            layout_djx = tmp_path / "layout.djx"
            layout_djx.write_text(layout_djx_content)

        class _FakeRouter:
            app_dirs = True
            pages_dir = "pages"

            def _get_installed_apps(self) -> list[str]:
                return ["app"]

            def _get_app_pages_path(self, _app: str) -> Path:
                return tmp_path

        with patch_checks_router_manager_with_routers(routers=[_FakeRouter()]):
            messages = check_page_functions(None)
            errors = [m for m in messages if m.id.startswith("next.E")]
            warnings = [m for m in messages if m.id.startswith("next.W")]
            assert len(errors) == expected_errors
            assert len(warnings) == expected_warnings
            if expected_warnings > 0:
                assert "has no content" in warnings[0].msg


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
        ids=["no_duplicates", "with_duplicates"],
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
        page_file = tmp_path / "page.py"
        page_file.write_text("")

        with patch_checks_router_manager(
            pages_directory=tmp_path,
            scan_routes=[(pattern, page_file) for pattern, _ in url_patterns],
        ):
            errors = check_duplicate_url_parameters(None)
            assert len(errors) == expected_errors

            if expected_errors > 0 and expected_error_msg:
                assert expected_error_msg in errors[0].msg
                if "duplicate parameter names" in expected_error_msg:
                    assert "id" in errors[0].msg


class TestContextFunctionsChecks:
    """Test cases for context functions checks."""

    def test_check_context_functions_valid_dict_return(self, tmp_path) -> None:
        """Test check_context_functions with valid dict return."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
def get_context_data():
    return {"key": "value"}
        """)

        with patch_checks_router_manager(
            pages_directory=tmp_path,
            scan_routes=[("test", page_file)],
        ) as (_mock_mgr, mock_router, _):
            mock_context_manager = MagicMock()
            mock_context_manager._context_registry = {
                page_file: {None: (lambda: {"key": "value"}, False)},
            }
            mock_router._context_manager = mock_context_manager

            errors = check_context_functions(None)
            assert len(errors) == 0

    def test_check_context_functions_invalid_return_type(self, tmp_path) -> None:
        """Test check_context_functions with invalid return type."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
def get_context_data():
    return "not a dict"
        """)

        with patch_checks_router_manager(
            pages_directory=tmp_path,
            scan_routes=[("test", page_file)],
        ):
            errors = check_context_functions(None)
            assert len(errors) == 1
            assert "must return a dictionary" in errors[0].msg
            assert "str" in errors[0].msg

    def test_check_context_functions_with_key_not_checked(self, tmp_path) -> None:
        """Test check_context_functions ignores functions with key."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context("my_key")
def get_context_data():
    return "not a dict but with key"
        """)

        with patch_checks_router_manager(
            pages_directory=tmp_path,
            scan_routes=[("test", page_file)],
        ) as (_mock_mgr, mock_router, _):
            mock_context_manager = MagicMock()
            mock_context_manager._context_registry = {
                page_file: {"my_key": (lambda: "not a dict but with key", False)},
            }
            mock_router._context_manager = mock_context_manager

            errors = check_context_functions(None)
            assert len(errors) == 0
