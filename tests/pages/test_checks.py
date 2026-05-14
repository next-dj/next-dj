from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

import next.pages.loaders as loaders_module
from next.checks import (
    _has_template_or_djx,
    check_context_functions,
    check_duplicate_url_parameters,
    check_layout_templates,
    check_page_functions,
)
from next.conf import next_framework_settings as s
from next.pages.checks import check_context_processor_signature, check_template_loaders
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


class TestCheckTemplateLoaders:
    """`check_template_loaders` validates `NEXT_FRAMEWORK['TEMPLATE_LOADERS']`."""

    def _run(self) -> list:

        return list(check_template_loaders())

    def _reset_loader_cache(self) -> None:

        loaders_module._REGISTERED_LOADERS_CACHE = None

    @override_settings(
        NEXT_FRAMEWORK={
            "TEMPLATE_LOADERS": ["next.pages.loaders.DjxTemplateLoader"],
        }
    )
    def test_valid_default_is_clean(self) -> None:

        s.reload()
        self._reset_loader_cache()
        assert self._run() == []

    @override_settings(NEXT_FRAMEWORK={"TEMPLATE_LOADERS": [123]})
    def test_non_string_entry_is_e042(self) -> None:

        s.reload()
        self._reset_loader_cache()
        msgs = self._run()
        assert len(msgs) == 1
        assert msgs[0].id == "next.E042"
        assert "dotted path string" in msgs[0].msg

    @override_settings(NEXT_FRAMEWORK={"TEMPLATE_LOADERS": ["does.not.exist.Loader"]})
    def test_unimportable_entry_is_e043(self) -> None:

        s.reload()
        self._reset_loader_cache()
        msgs = self._run()
        assert len(msgs) == 1
        assert msgs[0].id == "next.E043"
        assert "cannot be imported" in msgs[0].msg

    @override_settings(
        NEXT_FRAMEWORK={"TEMPLATE_LOADERS": ["next.pages.loaders.LayoutManager"]}
    )
    def test_non_subclass_entry_is_e043(self) -> None:

        s.reload()
        self._reset_loader_cache()
        msgs = self._run()
        assert len(msgs) == 1
        assert msgs[0].id == "next.E043"
        assert "not a TemplateLoader subclass" in msgs[0].msg


class TestBodySourceConflicts:
    """`check_page_functions` emits `next.W043` when two or more body sources coexist."""

    @pytest.mark.parametrize(
        (
            "test_case",
            "page_content",
            "create_template_djx",
            "expected_w043",
            "expected_winner",
            "expected_shadowed",
        ),
        [
            (
                "render_and_template_djx",
                'def render(request, **kwargs):\n    return "x"',
                True,
                1,
                "render()",
                "template.djx",
            ),
            (
                "render_and_template_attr",
                'template = "x"\ndef render(request, **kwargs):\n    return "x"',
                False,
                1,
                "render()",
                "template",
            ),
            (
                "template_attr_and_template_djx",
                'template = "x"',
                True,
                1,
                "template",
                "template.djx",
            ),
            (
                "all_three",
                'template = "x"\ndef render(request, **kwargs):\n    return "x"',
                True,
                1,
                "render()",
                "template, template.djx",
            ),
            (
                "only_render",
                'def render(request, **kwargs):\n    return "x"',
                False,
                0,
                None,
                None,
            ),
            (
                "only_template_attr",
                'template = "x"',
                False,
                0,
                None,
                None,
            ),
            (
                "only_template_djx",
                "",
                True,
                0,
                None,
                None,
            ),
        ],
        ids=[
            "render_and_template_djx",
            "render_and_template_attr",
            "template_attr_and_template_djx",
            "all_three",
            "only_render",
            "only_template_attr",
            "only_template_djx",
        ],
    )
    def test_w043_triggers_when_multiple_sources(
        self,
        tmp_path,
        test_case,
        page_content,
        create_template_djx,
        expected_w043,
        expected_winner,
        expected_shadowed,
    ) -> None:
        """Exercise the priority ordering and W043 payload."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)
        if create_template_djx:
            (tmp_path / "template.djx").write_text("<h1>body</h1>")

        class _FakeRouter:
            app_dirs = True
            pages_dir = "pages"

            def _get_installed_apps(self) -> list[str]:
                return ["app"]

            def _get_app_pages_path(self, _app: str) -> Path:
                return tmp_path

        with patch_checks_router_manager_with_routers(routers=[_FakeRouter()]):
            messages = check_page_functions(None)
            w043 = [m for m in messages if m.id == "next.W043"]
            assert len(w043) == expected_w043
            if expected_w043:
                msg = w043[0].msg
                assert f"{expected_winner} takes priority" in msg
                assert expected_shadowed in msg


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
        """Flag a keyless @context function annotated with a non-dict return."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
def get_context_data() -> str:
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

    def test_check_context_functions_unannotated_skipped(self, tmp_path) -> None:
        """Skip keyless @context functions with no return annotation."""
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
            assert errors == []

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


def _processor_with_request(request):
    return {}


def _processor_without_request():
    return {}


class TestContextProcessorSignature:
    """check_context_processor_signature warns when `request` is absent."""

    def test_empty_settings_produces_no_errors(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.pages.test_checks._processor_with_request",
                        ],
                    },
                },
            ],
        }
    )
    def test_processor_with_request_is_accepted(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.pages.test_checks._processor_without_request",
                        ],
                    },
                },
            ],
        }
    )
    def test_processor_without_request_triggers_error(self) -> None:
        errors = check_context_processor_signature()
        assert len(errors) == 1
        assert errors[0].id == "next.E040"
        assert "request" in errors[0].msg

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.pages.nonexistent.missing_processor",
                        ],
                    },
                },
            ],
        }
    )
    def test_unresolvable_processor_is_silently_skipped(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            123,  # non-string entry
                        ],
                    },
                },
            ],
        }
    )
    def test_non_string_processor_entries_are_skipped(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": "not a list"})
    def test_bad_settings_shape_is_tolerated(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_PAGE_BACKENDS": [
                "not a dict",
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {},
                },
            ],
        }
    )
    def test_non_dict_backend_entries_are_skipped(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []
