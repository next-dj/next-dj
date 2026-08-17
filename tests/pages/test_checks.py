from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.test import override_settings

import next.pages.loaders as loaders_module
from next.checks import (
    _has_template_or_djx,
    check_context_functions,
    check_layout_templates,
    check_page_functions,
    check_page_module_imports,
    check_pages_structure,
    check_request_in_context,
    check_single_keyless_context,
    reset_check_caches,
)
from next.conf import next_framework_settings as s
from next.pages.checks import (
    check_context_processor_signature,
    check_context_registration_files,
    check_template_loaders,
    check_unrouted_working_directory_pages,
)
from next.pages.manager import page
from next.pages.registry import PageContextRegistry
from next.urls import FileRouterBackend, PageRoot, RouterBackend
from tests.support import (
    MalformedRootsRouter,
    RaisingRootsRouter,
    RootPagesRouter,
    SkippingRouter,
    importable_dir,
    patch_checks_router_manager,
    patch_checks_router_manager_with_routers,
)


@pytest.fixture(autouse=True)
def _reset_check_caches():
    reset_check_caches()
    yield
    reset_check_caches()


class _AppRouter(RouterBackend):
    """Backend reporting one application tree, the shape `APP_DIRS` produces."""

    def __init__(self, pages_path: Path) -> None:
        self._pages_path = pages_path

    def generate_urls(self) -> list:
        return []

    def page_roots(self) -> list[PageRoot]:
        return [PageRoot(path=self._pages_path, label="App 'app'")]


class TestPageChecks:
    """Checks that decide whether a routed ``page.py`` can produce a body."""

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
        self, tmp_path, page_content, create_djx, djx_content, expected_result
    ) -> None:
        """Only a ``template`` attribute or a sibling ``template.djx`` counts, ``render()`` does not."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)

        if create_djx:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        result = _has_template_or_djx(page_file)
        assert result is expected_result


class TestRequestInContextCheck:
    """`next.E019` follows the framework app under either `INSTALLED_APPS` spelling."""

    def _templates_without_request(self) -> list[dict]:
        """Return a TEMPLATES list whose only engine omits the request processor."""
        return [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ]

    def _without_framework(self, installed) -> list[str]:
        """Drop every spelling of the framework app from an INSTALLED_APPS list."""
        return [app for app in installed if not app.startswith("next")]

    def test_plain_entry_reports_e019(self, settings) -> None:
        """The long-standing spelling keeps the report."""
        settings.TEMPLATES = self._templates_without_request()

        assert [m.id for m in check_request_in_context()] == ["next.E019"]

    def test_app_config_entry_reports_e019(self, settings) -> None:
        """The `AppConfig` spelling installs the same app, so the check still fires."""
        settings.INSTALLED_APPS = [
            *self._without_framework(settings.INSTALLED_APPS),
            "next.apps.NextFrameworkConfig",
        ]
        settings.TEMPLATES = self._templates_without_request()

        assert [m.id for m in check_request_in_context()] == ["next.E019"]

    def test_framework_not_installed_is_silent(self, settings) -> None:
        """A project without the framework app is none of this check's business."""
        settings.INSTALLED_APPS = self._without_framework(settings.INSTALLED_APPS)
        settings.TEMPLATES = self._templates_without_request()

        assert check_request_in_context() == []


class TestLayoutChecks:
    """Checks over ``layout.djx`` files found in the page trees."""

    @pytest.mark.parametrize(
        ("layout_body", "expected_warnings", "msg_substring"),
        [
            ("<html>{% block template %}{% endblock template %}</html>", 0, None),
            (
                "<html><body>No template block</body></html>",
                1,
                "does not contain required {% block template %}",
            ),
        ],
        ids=["with_block", "without_block"],
    )
    def test_check_layout_templates_scenarios(
        self, tmp_path, layout_body, expected_warnings, msg_substring
    ) -> None:
        """Layout.djx with or without required ``{% block template %}``."""
        (tmp_path / "layout.djx").write_text(layout_body)
        page_file = tmp_path / "page.py"
        page_file.write_text("")

        with patch_checks_router_manager(pages_directory=tmp_path):
            warnings = check_layout_templates(None)
        assert len(warnings) == expected_warnings
        if msg_substring is not None:
            assert msg_substring in warnings[0].msg

    def test_nested_roots_report_one_layout_once(self, tmp_path) -> None:
        # A root nested inside another reaches the same page twice, and the
        # layout is warned about once however many routes reach it.
        outer = tmp_path / "pages"
        inner = outer / "blog"
        inner.mkdir(parents=True)
        (inner / "layout.djx").write_text("<html><body>No template block</body></html>")
        (inner / "page.py").write_text('template = "ok"\n')

        router = _MultiRootRouter([outer, inner])
        with patch_checks_router_manager_with_routers(routers=[router]):
            warnings = check_layout_templates(None)

        assert [w.id for w in warnings] == ["next.W001"]


class TestMissingPageContentChecks:
    """``check_page_functions`` raises E012 and E013 for invalid page modules."""

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
            ("with_template_djx", "", True, "<h1>Hello World</h1>", False, None, 0, 0),
            (
                "with_layout_djx",
                "",
                False,
                None,
                True,
                "<html>{% block template %}{% endblock template %}</html>",
                0,
                0,
            ),
            ("no_content", "", False, None, False, None, 1, 0),
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
        """``check_page_functions`` fires the expected error and warning counts."""
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

            def page_roots(self) -> list[PageRoot]:
                return [PageRoot(path=tmp_path, label="App 'app'")]

        with patch_checks_router_manager_with_routers(routers=[_FakeRouter()]):
            messages = check_page_functions(None)
            errors = [m for m in messages if m.id.startswith("next.E")]
            warnings = [m for m in messages if m.id.startswith("next.W")]
            assert len(errors) == expected_errors
            assert len(warnings) == expected_warnings


class TestMemoAndBrokenPages:
    """Memo reuse and the E017-only reporting of broken `page.py` files."""

    def test_broken_page_reports_no_e012(self, tmp_path) -> None:
        """A syntactically invalid page.py surfaces as E017 only, never E012."""
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager_with_routers(routers=[_AppRouter(tmp_path)]):
            messages = check_page_functions(None)
        e012 = [m for m in messages if m.id == "next.E012"]
        assert e012 == []

    def test_valid_body_page_has_no_e012(self, tmp_path) -> None:
        """A page.py with a real render body raises no E012."""
        page_file = tmp_path / "page.py"
        page_file.write_text('def render(request, **kwargs):\n    return "x"\n')
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager_with_routers(routers=[_AppRouter(tmp_path)]):
            messages = check_page_functions(None)
        e012 = [m for m in messages if m.id == "next.E012"]
        assert e012 == []

    def test_empty_page_still_reports_e012(self, tmp_path) -> None:
        """A valid but bodyless page.py keeps the genuine no-body-source E012."""
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager_with_routers(routers=[_AppRouter(tmp_path)]):
            messages = check_page_functions(None)
        e012 = [m for m in messages if m.id == "next.E012"]
        assert len(e012) == 1

    def test_page_module_execed_once_across_checks(self, tmp_path, monkeypatch) -> None:
        """Two check passes over one page.py exec the module at most once per mtime."""
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "hi"\n')
        loaders_module._MODULE_MEMO.pop(page_file, None)

        real_load = loaders_module._load_python_module
        calls: list[Path] = []

        def counting(file_path: Path) -> object:
            calls.append(file_path)
            return real_load(file_path)

        monkeypatch.setattr(loaders_module, "_load_python_module", counting)

        router = _AppRouter(tmp_path)
        with (
            patch_checks_router_manager_with_routers(routers=[router]),
            patch("next.checks.common.get_pages_directories", return_value=[tmp_path]),
        ):
            check_page_functions(None)
            check_context_functions(None)

        assert calls.count(page_file) == 1


class TestCheckTemplateLoaders:
    """`check_template_loaders` validates `NEXT_FRAMEWORK['TEMPLATE_LOADERS']`."""

    def _run(self) -> list:

        return list(check_template_loaders())

    def _reset_loader_cache(self) -> None:
        # the cache is a single-slot holder mutated in place, never rebound,
        # so a stale None on this worker cannot break the production reads
        loaders_module._REGISTERED_LOADERS_CACHE["value"] = None

    @override_settings(
        NEXT_FRAMEWORK={"TEMPLATE_LOADERS": ["next.pages.loaders.DjxTemplateLoader"]}
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
        NEXT_FRAMEWORK={"TEMPLATE_LOADERS": ["next.pages.registry.PageContextRegistry"]}
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
            ("only_template_attr", 'template = "x"', False, 0, None, None),
            ("only_template_djx", "", True, 0, None, None),
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

            def page_roots(self) -> list[PageRoot]:
                return [PageRoot(path=tmp_path, label="App 'app'")]

        with patch_checks_router_manager_with_routers(routers=[_FakeRouter()]):
            messages = check_page_functions(None)
            w043 = [m for m in messages if m.id == "next.W043"]
            assert len(w043) == expected_w043
            if expected_w043:
                msg = w043[0].msg
                assert f"{expected_winner} takes priority" in msg
                assert expected_shadowed in msg


class TestContextFunctionsChecks:
    """Checks over the return shape of registered ``@context`` functions."""

    def test_check_context_functions_valid_dict_return(self, tmp_path) -> None:
        """A keyless ``@context`` returning a dict raises nothing."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
def get_context_data():
    return {"key": "value"}
        """)

        with patch_checks_router_manager(pages_directory=tmp_path) as (
            _mock_mgr,
            mock_router,
            _,
        ):
            mock_context_manager = MagicMock()
            mock_context_manager._context_registry = {
                page_file: {None: (lambda: {"key": "value"}, False)}
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

        with patch_checks_router_manager(pages_directory=tmp_path):
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

        with patch_checks_router_manager(pages_directory=tmp_path):
            errors = check_context_functions(None)
            assert errors == []

    def test_e029_on_page_context_attribute_form(self, tmp_path) -> None:
        """E029 fires on the canonical `@page.context` keyless form."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import page

@page.context
def get_context_data() -> str:
    return {}
        """)
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager(pages_directory=tmp_path):
            errors = check_context_functions(None)
        assert len(errors) == 1
        assert errors[0].id == "next.E029"

    def test_e029_on_async_keyless_context(self, tmp_path) -> None:
        """E029 fires on an `async def` keyless `@context` callable."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context
async def get_context_data() -> str:
    return {}
        """)
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager(pages_directory=tmp_path):
            errors = check_context_functions(None)
        assert len(errors) == 1
        assert errors[0].id == "next.E029"

    def test_e029_on_aliased_context_decorator(self, tmp_path) -> None:
        """E029 fires when the decorator is imported under an alias."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context as ctx

@ctx
def get_context_data() -> str:
    return {}
        """)
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager(pages_directory=tmp_path):
            errors = check_context_functions(None)
        assert len(errors) == 1
        assert errors[0].id == "next.E029"

    def test_e029_clears_after_context_decorator_removed(self, tmp_path) -> None:
        """Removing the keyless `@context` stops E029 once caches are reset."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import page

@page.context
def get_context_data() -> str:
    return {}
        """)
        loaders_module._MODULE_MEMO.pop(page_file, None)

        with patch_checks_router_manager(pages_directory=tmp_path):
            first = check_context_functions(None)
            assert [error.id for error in first] == ["next.E029"]

            page_file.write_text("""
def get_context_data():
    return {}
            """)
            reset_check_caches()
            second = check_context_functions(None)
        assert second == []

    def test_check_context_functions_with_key_not_checked(self, tmp_path) -> None:
        """A keyed ``@context`` may return any type and is left alone."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context("my_key")
def get_context_data():
    return "not a dict but with key"
        """)

        with patch_checks_router_manager(pages_directory=tmp_path) as (
            _mock_mgr,
            mock_router,
            _,
        ):
            mock_context_manager = MagicMock()
            mock_context_manager._context_registry = {
                page_file: {"my_key": (lambda: "not a dict but with key", False)}
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
            "PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.pages.test_checks._processor_with_request"
                        ]
                    },
                }
            ]
        }
    )
    def test_processor_with_request_is_accepted(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.pages.test_checks._processor_without_request"
                        ]
                    },
                }
            ]
        }
    )
    def test_processor_without_request_triggers_error(self) -> None:
        errors = check_context_processor_signature()
        assert len(errors) == 1
        assert errors[0].id == "next.E040"
        assert "request" in errors[0].msg

    @override_settings(
        NEXT_FRAMEWORK={
            "PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            "tests.pages.nonexistent.missing_processor"
                        ]
                    },
                }
            ]
        }
    )
    def test_unresolvable_processor_is_silently_skipped(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {
                        "context_processors": [
                            123  # non-string entry
                        ]
                    },
                }
            ]
        }
    )
    def test_non_string_processor_entries_are_skipped(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": "not a list"})
    def test_bad_settings_shape_is_tolerated(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []

    @override_settings(
        NEXT_FRAMEWORK={
            "PAGE_BACKENDS": [
                "not a dict",
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "PAGES_DIR": "pages",
                    "OPTIONS": {},
                },
            ]
        }
    )
    def test_non_dict_backend_entries_are_skipped(self) -> None:
        errors = check_context_processor_signature()
        assert errors == []


class _MultiRootRouter(RouterBackend):
    """Backend reporting several root trees, labelled the way the file router does."""

    def __init__(self, roots: list[Path]) -> None:
        self._roots = list(roots)

    def generate_urls(self) -> list:
        return []

    def page_roots(self) -> list[PageRoot]:
        return [
            PageRoot(path=root, label="Root" if index == 0 else f"Root ({root})")
            for index, root in enumerate(self._roots)
        ]


class TestPageModuleImports:
    """next.E017 flags a page.py that raises while importing."""

    def test_broken_page_reports_e017(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_module_imports(None)
        assert [m.id for m in messages] == ["next.E017"]

    def test_valid_page_reports_no_e017(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "ok"\n')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_module_imports(None)
        assert messages == []

    def test_broken_page_in_a_later_root_reports_e017(self, tmp_path) -> None:
        # The body-source checks stay silent on a broken import, so E017 is
        # the only report left for a page outside the first root.
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        healthy = first_root / "ok" / "page.py"
        healthy.parent.mkdir(parents=True)
        healthy.write_text('template = "ok"\n')
        broken = second_root / "bad" / "page.py"
        broken.parent.mkdir(parents=True)
        broken.write_text("def render( invalid syntax {\n")

        router = _MultiRootRouter([first_root, second_root])
        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(broken))]

    def test_symlinked_root_does_not_double_report_one_page(self, tmp_path) -> None:
        # The two roots spell one tree differently, and only the resolved
        # spelling reveals that a second walk would report the same file twice.
        real_root = tmp_path / "real"
        broken = real_root / "bad" / "page.py"
        broken.parent.mkdir(parents=True)
        broken.write_text("def render( invalid syntax {\n")
        linked_root = tmp_path / "linked"
        linked_root.symlink_to(real_root, target_is_directory=True)

        router = _MultiRootRouter([real_root, linked_root])
        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(broken))]

    def test_symlink_inside_one_tree_does_not_double_report(self, tmp_path) -> None:
        # One walk of one root reaches the same file under two spellings, so the
        # page identity has to be the resolved path rather than the walked one.
        real = tmp_path / "real"
        real.mkdir()
        (real / "page.py").write_text("def render( invalid syntax {\n")
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_page_module_imports(None)

        assert [m.id for m in messages] == ["next.E017"]

    def test_virtual_page_without_file_is_skipped(self, tmp_path) -> None:
        # A `template.djx` with no `page.py` beside it routes as a virtual page,
        # and the pair carries a path that never existed.
        (tmp_path / "virtual").mkdir()
        (tmp_path / "virtual" / "template.djx").write_text("<p>ok</p>\n")

        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_module_imports(None)
        assert messages == []

    def test_broken_page_beside_template_djx_is_caught_by_e017_not_e012(
        self, tmp_path
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        (tmp_path / "template.djx").write_text("<p>ok</p>\n")

        with patch_checks_router_manager_with_routers(routers=[_AppRouter(tmp_path)]):
            body = check_page_functions(None)
        assert [m for m in body if m.id == "next.E012"] == []

        with patch_checks_router_manager(pages_directory=tmp_path):
            imports = check_page_module_imports(None)
        assert [m.id for m in imports] == ["next.E017"]

    @override_settings(DEBUG=False)
    def test_broken_page_reports_e017_with_cause_in_ci_mode(self, tmp_path) -> None:
        """Both flags off (typical CI) still yields the enriched E017, no raise."""
        s.reload()
        assert s.STRICT_LOADING is False
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")

        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_module_imports(None)

        assert [m.id for m in messages] == ["next.E017"]
        error = loaders_module.last_load_error(page_file)
        assert error is not None
        expected = (
            f"page.py at {page_file} failed to import "
            f"({type(error.__cause__).__name__}: {error.__cause__}). "
            "A raising import in the module body counts the same as a "
            "syntax error. Fix it so the framework stops skipping the "
            "module silently."
        )
        assert messages[0].msg == expected

    def test_import_error_page_reports_e017_not_e012(self, tmp_path) -> None:
        """A failing import in the page body is named ImportError, not no-body E012."""
        page_file = tmp_path / "page.py"
        page_file.write_text("import missing_dep_xyz\n")

        with patch_checks_router_manager_with_routers(routers=[_AppRouter(tmp_path)]):
            body = check_page_functions(None)
        assert [m for m in body if m.id == "next.E012"] == []

        with patch_checks_router_manager(pages_directory=tmp_path):
            imports = check_page_module_imports(None)
        assert [m.id for m in imports] == ["next.E017"]
        assert "ModuleNotFoundError" in imports[0].msg
        assert "missing_dep_xyz" in imports[0].msg

    @pytest.mark.parametrize(
        ("page_body", "cause_marker"),
        [
            ("def render( invalid syntax {\n", "SyntaxError"),
            (
                "import missing_dep_xyz\n",
                "ModuleNotFoundError: No module named 'missing_dep_xyz'",
            ),
        ],
        ids=["syntax_error", "module_not_found"],
    )
    def test_e017_message_distinguishes_causes(
        self, tmp_path, page_body, cause_marker
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(page_body)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_module_imports(None)
        assert [m.id for m in messages] == ["next.E017"]
        assert cause_marker in messages[0].msg

    @pytest.mark.parametrize(
        "page_body",
        [
            'template = "x"\nimport missing_dep_xyz\n',
            "render = 42\nimport missing_dep_xyz\n",
        ],
        ids=["would_be_w043", "would_be_e013"],
    )
    def test_broken_import_skips_body_source_checks(self, tmp_path, page_body) -> None:
        """E012, E013, and W043 stay silent when the import itself failed."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_body)
        (tmp_path / "template.djx").write_text("<p>ok</p>\n")

        with patch_checks_router_manager_with_routers(routers=[_AppRouter(tmp_path)]):
            messages = check_page_functions(None)
        assert [
            m for m in messages if m.id in {"next.E012", "next.E013", "next.W043"}
        ] == []


class TestSingleKeylessContext:
    """next.E018 flags a page.py with more than one keyless @context."""

    def test_two_keyless_contexts_report_e018(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n"
            "@page.context\n"
            "def first() -> dict:\n"
            "    return {}\n\n"
            "@page.context\n"
            "def second() -> dict:\n"
            "    return {}\n"
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_single_keyless_context(None)
        assert [m.id for m in messages] == ["next.E018"]
        assert "first" in messages[0].msg
        assert "second" in messages[0].msg

    def test_single_keyless_context_reports_nothing(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n"
            "@page.context\n"
            "def only() -> dict:\n"
            "    return {}\n"
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_single_keyless_context(None)
        assert messages == []


class TestContextChecksDeduplicate:
    """Checks report a page surfaced by several routers once, not per router."""

    def test_e029_reported_once_across_routers(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n"
            "@page.context\n"
            "def bad() -> str:\n"
            "    return {}\n"
        )
        routers = [_MultiRootRouter([tmp_path]), _MultiRootRouter([tmp_path])]
        with patch_checks_router_manager_with_routers(routers=routers):
            messages = check_context_functions(None)
        assert [m.id for m in messages] == ["next.E029"]

    def test_e029_reported_once_for_a_symlinked_spelling(self, tmp_path) -> None:
        # The registry keys on the spelling importlib loaded, so the check has to
        # dedupe on the resolved path while still looking the page up as walked.
        real = tmp_path / "real"
        real.mkdir()
        page_file = real / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n"
            "@page.context\n"
            "def bad() -> str:\n"
            "    return {}\n"
        )
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_context_functions(None)

        root = tmp_path.resolve()
        assert [m.id for m in messages] == ["next.E029"]
        assert messages[0].obj in {
            str(root / "real" / "page.py"),
            str(root / "link" / "page.py"),
        }


class TestContextRegistrationFileCheck:
    """`check_context_registration_files` catches a context bound to another file."""

    def test_context_on_imported_helper_is_e074(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import context\n"
            "from tests.support.attribution import handler_declared_here\n\n"
            "context('greeting')(handler_declared_here)\n"
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with (
            patch.object(page, "_context_manager", PageContextRegistry(None)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_context_registration_files(None)
        assert [m.id for m in messages] == ["next.E074"]
        assert "attribution.py" in messages[0].msg
        assert "handler_declared_here" in messages[0].msg
        assert str(page_file) in messages[0].msg

    def test_context_from_another_page_py_is_e074(self, tmp_path) -> None:
        donor_dir = tmp_path / "donor"
        donor_dir.mkdir()
        (donor_dir / "__init__.py").write_text("")
        (donor_dir / "page.py").write_text("def donated() -> str:\n    return 'hi'\n")
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import context\n"
            "from donor.page import donated\n\n"
            "context('greeting')(donated)\n"
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with (
            patch.object(page, "_context_manager", PageContextRegistry(None)),
            patch_checks_router_manager(pages_directory=tmp_path),
            importable_dir(tmp_path),
        ):
            messages = check_context_registration_files(None)
        assert [m.id for m in messages] == ["next.E074"]
        assert "donated" in messages[0].msg
        assert str(donor_dir / "page.py") in messages[0].msg

    def test_e074_names_the_function_behind_a_partial(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "import functools\n\n"
            "from next.pages import context\n"
            "from tests.support.attribution import handler_declared_here\n\n"
            "context('greeting')(functools.partial(handler_declared_here))\n"
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with (
            patch.object(page, "_context_manager", PageContextRegistry(None)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_context_registration_files(None)
        assert [m.id for m in messages] == ["next.E074"]
        assert "handler_declared_here" in messages[0].msg

    def test_context_declared_in_the_page_file_reports_nothing(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import context\n\n"
            "@context('greeting')\n"
            "def greeting() -> str:\n"
            "    return 'hi'\n"
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with (
            patch.object(page, "_context_manager", PageContextRegistry(None)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_context_registration_files(None)
        assert messages == []


class TestRouterFailureIsReportedNotRaised:
    """A third-party router that cannot list its trees is a message, not a crash."""

    def test_a_raising_tree_listing_is_one_e030(self) -> None:
        with patch_checks_router_manager_with_routers(routers=[RaisingRootsRouter()]):
            messages = check_pages_structure(None)

        assert [m.id for m in messages] == ["next.E030"]
        assert "database is down" in messages[0].msg
        assert "RaisingRootsRouter" in messages[0].msg

    def test_every_other_page_check_stays_quiet(self) -> None:
        # The finding belongs to one check, so the rest of the run degrades to
        # "this router reports no tree" instead of repeating the failure.
        with patch_checks_router_manager_with_routers(routers=[RaisingRootsRouter()]):
            assert check_page_functions(None) == []
            assert check_page_module_imports(None) == []
            assert check_layout_templates(None) == []
            assert check_context_functions(None) == []

    def test_a_healthy_router_beside_it_keeps_its_reports(self, tmp_path) -> None:
        (tmp_path / "bare").mkdir()
        (tmp_path / "bare" / "page.py").write_text("")
        routers = [RaisingRootsRouter(), RootPagesRouter([tmp_path])]

        with patch_checks_router_manager_with_routers(routers=routers):
            structure = check_pages_structure(None)
            functions = check_page_functions(None)

        assert [m.id for m in structure] == ["next.E030"]
        assert [m.id for m in functions] == ["next.E012"]

    def test_a_wrong_tree_shape_is_one_e030_without_a_cause(self, tmp_path) -> None:
        with patch_checks_router_manager_with_routers(
            routers=[MalformedRootsRouter([tmp_path])]
        ):
            messages = check_pages_structure(None)

        assert [m.id for m in messages] == ["next.E030"]
        assert "MalformedRootsRouter" in messages[0].msg
        assert "None" not in messages[0].msg

    def test_the_checks_that_walk_survive_a_wrong_tree_shape(self, tmp_path) -> None:
        # These read the trees through the swallowing seam, so a bare path
        # costs the router its reports instead of ending the run.
        with patch_checks_router_manager_with_routers(
            routers=[MalformedRootsRouter([tmp_path])]
        ):
            assert check_layout_templates(None) == []
            assert check_unrouted_working_directory_pages(None) == []


class TestRealFileRouterBackend:
    """The real file router drives the page checks over both kinds of root."""

    def _build_project(self, tmp_path: Path) -> Path:
        app_pages = tmp_path / "shop_app" / "pages"
        (tmp_path / "shop_app" / "__init__.py").parent.mkdir(parents=True)
        (tmp_path / "shop_app" / "__init__.py").write_text("")
        (app_pages / "hollow").mkdir(parents=True)
        (app_pages / "hollow" / "page.py").write_text("")
        (app_pages / "broken").mkdir()
        (app_pages / "broken" / "page.py").write_text("def render( invalid syntax {\n")
        dirs_root = tmp_path / "shell"
        (dirs_root / "bare").mkdir(parents=True)
        (dirs_root / "bare" / "page.py").write_text("")
        return dirs_root

    def test_app_tree_and_dirs_root_both_report(self, tmp_path, settings) -> None:
        """An APP_DIRS router reports its app trees and its DIRS roots alike."""
        dirs_root = self._build_project(tmp_path)
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[dirs_root])

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop_app"]
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_page_functions(None)

        e012 = [m for m in messages if m.id == "next.E012"]
        assert [m.msg.split(" has no body source")[0] for m in e012] == [
            "App 'shop_app' pages: hollow/page.py",
            "Root pages: bare/page.py",
        ]

    def test_tree_reachable_as_app_and_root_reports_once(
        self, tmp_path, settings
    ) -> None:
        """A DIRS entry pointing at an app tree does not double every report."""
        self._build_project(tmp_path)
        app_pages = tmp_path / "shop_app" / "pages"
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[app_pages])

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop_app"]
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_page_functions(None)

        e012 = [m for m in messages if m.id == "next.E012"]
        assert [m.msg.split(" has no body source")[0] for m in e012] == [
            "App 'shop_app' pages: hollow/page.py"
        ]

    def test_nested_dirs_root_reports_each_page_once(self, tmp_path, settings) -> None:
        """A DIRS root inside an app tree reaches one page twice, reported once."""
        self._build_project(tmp_path)
        nested = tmp_path / "shop_app" / "pages" / "hollow"
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[nested])

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop_app"]
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_page_functions(None)

        e012 = [m for m in messages if m.id == "next.E012"]
        assert [m.msg.split(" has no body source")[0] for m in e012] == [
            "App 'shop_app' pages: hollow/page.py"
        ]

    def test_nested_dirs_root_reports_each_directory_once(
        self, tmp_path, settings
    ) -> None:
        """The structure check names a parameter directory once per directory."""
        app_pages = tmp_path / "shop_app" / "pages"
        (app_pages / "items" / "[id]").mkdir(parents=True)
        (tmp_path / "shop_app" / "__init__.py").write_text("")
        router = FileRouterBackend(
            app_dirs=True, extra_root_paths=[app_pages / "items"]
        )

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop_app"]
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_pages_structure(None)

        e010 = [m for m in messages if m.id == "next.E010"]
        assert [m.msg for m in e010] == [
            (
                "App 'shop_app' pages: Parameter directory \"items/[id]\" "
                "is missing page.py file."
            )
        ]

    def test_third_party_backend_reports_the_pages_of_its_trees(self, tmp_path) -> None:
        """A backend that names its trees through `page_roots` is checked like any."""
        (tmp_path / "bare").mkdir()
        (tmp_path / "bare" / "page.py").write_text("")

        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            messages = check_page_functions(None)

        e012 = [m for m in messages if m.id == "next.E012"]
        assert [m.msg.split(" has no body source")[0] for m in e012] == [
            "Root pages: bare/page.py"
        ]

    def test_broken_page_in_the_app_tree_reports_e017(self, tmp_path, settings) -> None:
        """The real discovery of an app tree still surfaces a raising import."""
        dirs_root = self._build_project(tmp_path)
        broken = tmp_path / "shop_app" / "pages" / "broken" / "page.py"
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[dirs_root])

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop_app"]
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(broken))]


class TestSkippedDirectoriesLeaveThePageChecks:
    """A directory the router refuses answers no URL, so no page check names it."""

    def _write_bodyless_page(self, tree: Path, route: str) -> Path:
        directory = tree / route
        directory.mkdir(parents=True, exist_ok=True)
        page_file = directory / "page.py"
        page_file.write_text("")
        return page_file

    def test_the_components_folder_a_backend_names_is_skipped(self, tmp_path) -> None:
        """A page.py inside the components folder is no route and no report."""
        self._write_bodyless_page(tmp_path, "_components/card")
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            assert check_page_functions(None) == []
            assert check_pages_structure(None) == []

    def test_a_dirs_segment_the_file_router_refuses_is_skipped(self, tmp_path) -> None:
        """The skip set the file router builds from DIRS holds for the checks too."""
        self._write_bodyless_page(tmp_path, "drafts/wip")
        router = FileRouterBackend(
            app_dirs=False, extra_root_paths=[tmp_path, Path("drafts")]
        )

        with patch_checks_router_manager_with_routers(routers=[router]):
            assert check_page_functions(None) == []

    def test_a_skip_name_the_router_declares_leaves_the_body_check(
        self, tmp_path
    ) -> None:
        """A directory the backend's own walk refuses keeps its pages unchecked."""
        self._write_bodyless_page(tmp_path, "drafts/wip")

        with patch_checks_router_manager_with_routers(
            routers=[SkippingRouter([tmp_path], frozenset({"drafts"}))]
        ):
            assert check_page_functions(None) == []

        reset_check_caches()
        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            assert [m.id for m in check_page_functions(None)] == ["next.E012"]

    def test_a_skip_name_the_router_declares_leaves_the_structure_check(
        self, tmp_path
    ) -> None:
        """A parameter directory below a refused name raises no structural report."""
        (tmp_path / "drafts" / "[id]").mkdir(parents=True)

        with patch_checks_router_manager_with_routers(
            routers=[SkippingRouter([tmp_path], frozenset({"drafts"}))]
        ):
            assert check_pages_structure(None) == []

        reset_check_caches()
        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            assert [m.id for m in check_pages_structure(None)] == ["next.E010"]

    def test_a_child_route_under_a_skip_name_does_not_answer_for_e010(
        self, tmp_path
    ) -> None:
        """A page under a refused name is no child route, so the parameter dir is bare."""
        self._write_bodyless_page(tmp_path, "items/[id]/drafts")

        with patch_checks_router_manager_with_routers(
            routers=[SkippingRouter([tmp_path], frozenset({"drafts"}))]
        ):
            messages = check_pages_structure(None)
        assert [m.id for m in messages] == ["next.E010"]
        assert 'Parameter directory "items/[id]"' in messages[0].msg

        reset_check_caches()
        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            assert check_pages_structure(None) == []


class TestThirdPartyBackendReachesEverySeam:
    """A backend that only reports its trees is checked like the file router."""

    def _write_project(self, tree: Path) -> Path:
        broken = tree / "broken"
        broken.mkdir(parents=True)
        (broken / "page.py").write_text("def render( invalid syntax {\n")
        (broken / "layout.djx").write_text("<html><body>no block</body></html>")
        return broken / "page.py"

    def test_the_import_check_sees_its_pages(self, tmp_path) -> None:
        # next.E017 reaches the pairs through the framework's own walk, so a
        # backend that walks nothing of its own is still covered.
        broken = self._write_project(tmp_path)

        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            messages = check_page_module_imports(None)

        assert [(m.id, m.obj) for m in messages] == [("next.E017", str(broken))]

    def test_the_layout_check_sees_its_layouts(self, tmp_path) -> None:
        self._write_project(tmp_path)

        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            warnings = check_layout_templates(None)

        assert [w.id for w in warnings] == ["next.W001"]

    def test_the_keyless_context_checks_see_its_pages(self, tmp_path) -> None:
        page_file = tmp_path / "hello" / "page.py"
        page_file.parent.mkdir()
        page_file.write_text(
            "from next.pages import page\n\n"
            "@page.context\n"
            "def first() -> str:\n"
            "    return {}\n\n"
            "@page.context\n"
            "def second() -> str:\n"
            "    return {}\n"
        )

        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            annotations = check_context_functions(None)
            keyless = check_single_keyless_context(None)

        assert [m.id for m in annotations] == ["next.E029"]
        assert [m.id for m in keyless] == ["next.E018"]

    def test_the_registration_file_check_sees_its_pages(self, tmp_path) -> None:
        page_file = tmp_path / "hello" / "page.py"
        page_file.parent.mkdir()
        page_file.write_text(
            "from next.pages import page\n"
            "from tests.support.attribution import handler_declared_here\n\n"
            "page.context('greeting')(handler_declared_here)\n"
        )

        with (
            patch.object(page, "_context_manager", PageContextRegistry()),
            patch_checks_router_manager_with_routers(
                routers=[RootPagesRouter([tmp_path])]
            ),
        ):
            messages = check_context_registration_files(None)

        assert [m.id for m in messages] == ["next.E074"]


class TestUnroutedWorkingDirectoryPages:
    """`next.W002` names a pages tree beside the process that nobody routes."""

    def _write_unrouted_tree(self, root: Path) -> Path:
        pages = root / "pages" / "hello"
        pages.mkdir(parents=True)
        (pages / "page.py").write_text('template = "ok"\n')
        return root / "pages"

    def test_an_unrouted_tree_is_named_once(self, tmp_path, monkeypatch) -> None:
        directory = self._write_unrouted_tree(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            router = FileRouterBackend(app_dirs=False)
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_unrouted_working_directory_pages(None)

        assert [(m.id, m.obj) for m in messages] == [
            ("next.W002", str(directory.resolve()))
        ]
        assert "no configured router routes" in messages[0].msg
        assert "BASE_DIR" in messages[0].msg
        assert "DIRS" in messages[0].msg

    def test_the_page_checks_leave_the_unrouted_tree_alone(
        self, tmp_path, monkeypatch
    ) -> None:
        # The tree stops being walked, so its contents no longer reach
        # next.E012 or next.E017. One warning stands for the lot.
        self._write_unrouted_tree(tmp_path)
        (tmp_path / "pages" / "bare").mkdir()
        (tmp_path / "pages" / "bare" / "page.py").write_text("")
        monkeypatch.chdir(tmp_path)

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            router = FileRouterBackend(app_dirs=False)
            with patch_checks_router_manager_with_routers(routers=[router]):
                functions = check_page_functions(None)
                structure = check_pages_structure(None)

        assert functions == []
        assert structure == []

    def test_a_tree_routed_through_dirs_is_silent(self, tmp_path, monkeypatch) -> None:
        directory = self._write_unrouted_tree(tmp_path)
        monkeypatch.chdir(tmp_path)

        router = FileRouterBackend(app_dirs=False, extra_root_paths=[directory])
        with patch_checks_router_manager_with_routers(routers=[router]):
            assert check_unrouted_working_directory_pages(None) == []

    def test_a_tree_routed_through_base_dir_is_silent(
        self, tmp_path, monkeypatch
    ) -> None:
        self._write_unrouted_tree(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch("next.utils.settings", Mock(BASE_DIR=tmp_path)):
            router = FileRouterBackend(app_dirs=False)
            with patch_checks_router_manager_with_routers(routers=[router]):
                assert check_unrouted_working_directory_pages(None) == []

    def test_a_project_without_the_directory_is_silent(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            router = FileRouterBackend(app_dirs=False)
            with patch_checks_router_manager_with_routers(routers=[router]):
                assert check_unrouted_working_directory_pages(None) == []

    def test_a_directory_holding_no_page_is_silent(self, tmp_path, monkeypatch) -> None:
        # An application package that happens to carry the PAGES_DIR name has
        # no page under it, and naming it would be noise.
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "__init__.py").write_text("")
        (tmp_path / "pages" / "models.py").write_text("")
        monkeypatch.chdir(tmp_path)

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            router = FileRouterBackend(app_dirs=False)
            with patch_checks_router_manager_with_routers(routers=[router]):
                assert check_unrouted_working_directory_pages(None) == []

    def test_an_application_named_pages_is_silent(self, tmp_path, monkeypatch) -> None:
        # `pages/pages/` is the routed tree of an application called `pages`,
        # so the directory above it is part of a served layout.
        app_pages = tmp_path / "pages" / "pages" / "hello"
        app_pages.mkdir(parents=True)
        (app_pages / "page.py").write_text('template = "ok"\n')
        monkeypatch.chdir(tmp_path)

        router = FileRouterBackend(
            app_dirs=False, extra_root_paths=[tmp_path / "pages" / "pages"]
        )
        with patch_checks_router_manager_with_routers(routers=[router]):
            assert check_unrouted_working_directory_pages(None) == []

    def test_a_failing_router_manager_reports_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        self._write_unrouted_tree(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "next.pages.checks.get_router_manager", return_value=(None, [Mock()])
        ):
            assert check_unrouted_working_directory_pages(None) == []

    def test_a_backend_naming_no_pages_dir_contributes_no_candidate(
        self, tmp_path, monkeypatch
    ) -> None:
        self._write_unrouted_tree(tmp_path)
        monkeypatch.chdir(tmp_path)
        entry = {"BACKEND": "tests.support.routers.RootPagesRouter"}

        with (
            override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}),
            patch_checks_router_manager_with_routers(routers=[RootPagesRouter([])]),
        ):
            s.reload()
            assert check_unrouted_working_directory_pages(None) == []
