import importlib.util
import os
import textwrap
import traceback
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest
from django.http import Http404, HttpRequest, HttpResponse
from django.template import Template
from django.template.response import TemplateResponse
from django.test import Client, RequestFactory, override_settings

import next.pages.loaders as loaders_module
from next.pages import Page, context, page
from next.pages.loaders import (
    NO_BOUNDARIES,
    LayoutTemplateLoader,
    PageModuleImportError,
    TemplateLoader,
    _load_python_module,
    build_registered_loaders,
    load_python_module_memo,
    page_boundaries,
)
from next.pages.registry import PageContextRegistry
from next.static import default_manager as static_default_manager
from next.testing import assert_metadata, override_next_settings
from next.views import page_view
from tests.support import (
    PAGE_RENDER_CASES,
    WITH_BASE,
    PageRenderCase,
    build_nested_page,
    build_page_request,
    path_under,
    record_path_calls,
    routed,
    touch_later,
    unified_view,
    write_page,
)


_BROKEN_SYNTAX = "def render( invalid syntax {\n"


_BROKEN_IMPORT = "import missing_dep_xyz\n"


_broken_sources = pytest.mark.parametrize(
    "broken_source",
    [_BROKEN_SYNTAX, _BROKEN_IMPORT],
    ids=["syntax_error", "import_error"],
)


ROOT = 'metadata = {"title": {"template": "{title} | Root"}, "description": "Root"}\n'


HEADED = Path(__file__).resolve().parent.parent / "site_pages" / "headed" / "page.py"


PAGED = {"METADATA": {"CANONICAL_QUERY": ("page",)}}


def _render(query: str = "") -> str:
    with override_next_settings(**WITH_BASE):
        response = unified_view(page, HEADED)(RequestFactory().get(f"/headed/{query}"))
    assert isinstance(response, HttpResponse | TemplateResponse)
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture(scope="module")
def html() -> str:
    return _render()


HEAD = "<head>{% metadata %}</head>"


class TestPage:
    """Registration, context collection, and rendering on a fresh ``Page``."""

    def test_init(self, page_instance) -> None:
        """A fresh ``Page`` starts with empty registries and its own layout loader."""
        assert not page_instance._templates.composed
        assert isinstance(page_instance.contexts, PageContextRegistry)
        assert isinstance(page_instance._layout_loader, LayoutTemplateLoader)

    def test_register_template_direct(self, page_instance) -> None:
        """``register_template`` stores the source under the exact path it was given."""
        file_path = Path("/test/path/page.py")
        template_str = "Hello {{ name }}!"

        page_instance.register_template(file_path, template_str)

        assert file_path in page_instance._templates.composed
        assert page_instance._templates.composed[file_path] == template_str

    def test_clear_template_caches_recomposes_a_rewritten_page(
        self, page_instance, tmp_path
    ) -> None:
        """The public drop makes the next compose read the file from disk again."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        template = tmp_path / "template.djx"
        template.write_text("<p>first</p>")
        assert "first" in page_instance.composed_template_for(page_file).source

        # Same mtime tick would otherwise hide the rewrite from the staleness check.
        template.write_text("<p>second</p>")
        os.utime(template, (0, 0))
        page_instance.clear_template_caches()

        assert "second" in page_instance.composed_template_for(page_file).source

    def test_clear_template_caches_empties_every_cache(self, page_instance) -> None:
        """One call drops every composed layer and the mtimes behind them."""
        file_path = Path("/test/path/page.py")
        page_instance.register_template(file_path, "<p>x</p>")
        page_instance._templates.compiled[file_path] = Template("<p>x</p>")
        page_instance._templates.composed_sources[file_path] = {}
        page_instance._templates.skeleton[file_path] = "<p>x</p>"
        page_instance._templates.skeleton_sources[file_path] = {}

        page_instance.clear_template_caches()

        assert not page_instance._templates.composed
        assert not page_instance._templates.compiled
        assert not page_instance._templates.composed_sources
        assert not page_instance._templates.skeleton
        assert not page_instance._templates.skeleton_sources

    @pytest.mark.parametrize("case", PAGE_RENDER_CASES, ids=lambda case: case.id)
    def test_render_scenarios(
        self, page_instance, test_file_path, case: PageRenderCase
    ) -> None:
        """``render`` merges the registered context functions with the caller kwargs."""
        page_instance.register_template(test_file_path, case.template)
        for key, func in case.context.items():
            page_instance.contexts.register_context(test_file_path, key, func)

        assert (
            page_instance.render(test_file_path, url_kwargs=case.kwargs)
            == case.expected
        )

    def test_render_with_multiple_files(self, page_instance) -> None:
        """Two page paths keep separate templates and context, with no cross-talk."""
        file1 = Path("/test/path/page1.py")
        template1 = "Page 1: {{ title }}"
        page_instance.register_template(file1, template1)
        page_instance.contexts.register_context(file1, "title", lambda: "First Page")

        file2 = Path("/test/path/page2.py")
        template2 = "Page 2: {{ title }}"
        page_instance.register_template(file2, template2)
        page_instance.contexts.register_context(file2, "title", lambda: "Second Page")

        result1 = page_instance.render(file1)
        result2 = page_instance.render(file2)

        assert result1 == "Page 1: First Page"
        assert result2 == "Page 2: Second Page"

    def test_render_with_inherited_context(self, page_instance, tmp_path) -> None:
        """A child page reads a parent ``page.py`` value marked ``inherit_context``."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text("<html>{% template %}</html>")

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        template_str = "Child page: {{ inherited_var }}"
        page_instance.register_template(child_page_file, template_str)

        def layout_func() -> str:
            return "inherited_value"

        page_instance.contexts.register_context(
            page_file, "inherited_var", layout_func, inherit_context=True
        )

        result = page_instance.render(child_page_file)

        assert "Child page: inherited_value" in result

    def test_render_with_inherited_context_override(
        self, page_instance, tmp_path
    ) -> None:
        """A child page value shadows the inherited one under the same key."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text("<html>{% template %}</html>")

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        template_str = "Child page: {{ var }}"
        page_instance.register_template(child_page_file, template_str)

        def layout_func() -> str:
            return "layout_value"

        page_instance.contexts.register_context(
            page_file, "var", layout_func, inherit_context=True
        )

        def child_func() -> str:
            return "child_value"

        page_instance.contexts.register_context(
            child_page_file, "var", child_func, inherit_context=False
        )

        result = page_instance.render(child_page_file)

        assert "Child page: child_value" in result


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
        assert page_file not in page_instance._templates.composed
        result = page_instance.render(page_file, url_kwargs={"title": "Lazy"})
        assert page_file in page_instance._templates.composed
        assert "Lazy" in result

    def test_render_with_no_body_source_returns_empty_block(
        self, page_instance, tmp_path
    ) -> None:
        """Page.render returns an empty placeholder when no body source exists."""
        page_file = tmp_path / "page.py"
        page_file.write_text("y = 1")
        result = page_instance.render(page_file)
        assert result == ""

    def test_render_invalidates_cache_when_template_stale(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """When source .djx mtime changes, render() reloads template."""
        page_file = tmp_path / "page.py"
        page_file.write_text("z = 3")
        djx = tmp_path / "template.djx"
        djx.write_text("<h1>{{ title }}</h1>")
        result1 = page_instance.render(page_file, url_kwargs={"title": "First"})
        assert "First" in result1
        djx.write_text("<h2>{{ title }}</h2>")
        result2 = page_instance.render(page_file, url_kwargs={"title": "Second"})
        assert "<h2>Second</h2>" in result2

    def test_render_injects_current_template_path_in_context(
        self, page_instance, tmp_path
    ) -> None:
        """render() puts current_template_path in the context for component lookup."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("path={{ current_template_path }}")
        result = page_instance.render(page_file)
        assert "current_template_path" in result or str(tmp_path) in result
        assert "path=" in result

    def test_render_forwards_request_to_static_inject(
        self, page_instance, tmp_path
    ) -> None:
        """render() passes the HttpRequest through to StaticManager.inject."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("hello")

        request = HttpRequest()
        request.method = "GET"

        with mock.patch.object(
            static_default_manager, "inject", return_value="hello"
        ) as inject_mock:
            page_instance.render(page_file, request)

        assert inject_mock.call_args.kwargs["request"] is request

    def test_render_forwards_request_via_keyword(self, page_instance, tmp_path) -> None:
        """render() also accepts the HttpRequest under the `request` keyword."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("hello")

        request = HttpRequest()
        request.method = "GET"

        with mock.patch.object(
            static_default_manager, "inject", return_value="hello"
        ) as inject_mock:
            page_instance.render(page_file, request=request)

        assert inject_mock.call_args.kwargs["request"] is request


class TestHasTemplateUnderALayout:
    """A page with an ancestor layout has a template to render."""

    def test_has_template_returns_true_when_ancestor_layout_exists(
        self, page_instance, tmp_path
    ) -> None:
        """`has_template` short-circuits to True when an ancestor layout applies."""
        (tmp_path / "layout.djx").write_text("<main>{% template %}</main>")
        sub = tmp_path / "sub"
        sub.mkdir()
        page_file = sub / "page.py"
        page_file.write_text("")
        assert page_instance.has_template(page_file, module=None) is True


class TestGlobalPageInstance:
    """The module-level ``page`` singleton and its ``context`` alias."""

    @pytest.fixture(autouse=True)
    def clear_global_state(self):
        """Give each test a clean global page state, then restore the baseline.

        The global `page` holds URL-conf-time providers a bare clear would drop.
        """
        template_snapshot = {
            path: page._templates.composed[path] for path in page._templates.composed
        }
        context_snapshot = {
            path: dict(entries)
            for path, entries in page.contexts._context_registry.items()
        }
        page._templates.composed.clear()
        page.contexts._context_registry.clear()
        yield
        page._templates.composed.clear()
        for path, source in template_snapshot.items():
            page._templates.composed[path] = source
        page.contexts._context_registry.clear()
        page.contexts._context_registry.update(context_snapshot)

    def test_global_page_instance(self) -> None:
        """The exported ``page`` is a ``Page`` carrying the same registries."""
        assert page is not None
        assert isinstance(page, Page)
        assert not page._templates.composed
        assert not page.contexts._context_registry

    def test_context_alias(self) -> None:
        """The exported ``context`` is the singleton's own bound decorator."""
        assert context == page.context

    def test_global_page_template_registration(self, global_file_path) -> None:
        """A template registered on the singleton is stored in its registry."""
        template_str = "Global template: {{ message }}"
        page.register_template(global_file_path, template_str)

        assert global_file_path in page._templates.composed
        assert page._templates.composed[global_file_path] == template_str

    def test_global_page_context_registration(self) -> None:
        """A context function registered on the singleton is stored in its registry."""

        @page.context("global_key")
        def get_global_value() -> str:
            return "global_value"

        registry = page.contexts._context_registry
        assert Path(__file__) in registry
        assert "global_key" in registry[Path(__file__)]

    def test_global_page_render(self) -> None:
        """The singleton renders a registered template with its own context."""
        page.register_template(Path(__file__), "Global: {{ key }}")

        @page.context("key")
        def get_key() -> str:
            return "value"

        result = page.render(Path(__file__))
        assert result == "Global: value"

    def test_context_decorator_with_global_page(self) -> None:
        """The exported ``context`` decorator registers against the declaring file."""

        @context("test_key")
        def test_function() -> str:
            return "test_value"

        registry = page.contexts._context_registry
        assert Path(__file__) in registry
        assert "test_key" in registry[Path(__file__)]
        entry = registry[Path(__file__)]["test_key"]
        assert entry.func == test_function
        assert entry.inherit_context is False
        assert entry.serialize is False

    def test_context_registered_from_another_module_keys_on_that_module(
        self, tmp_path
    ) -> None:
        """A page module registers under its own file, not under this test file."""
        script = tmp_path / "page.py"
        script.write_text(
            textwrap.dedent(
                """
                from next.pages import context

                @context("from_page_module")
                def get_value():
                    return "value"
                """
            ).lstrip()
        )
        spec = importlib.util.spec_from_file_location("dyn_page_ctx", script)
        assert spec is not None
        assert spec.loader is not None
        spec.loader.exec_module(importlib.util.module_from_spec(spec))

        registry = page.contexts._context_registry
        assert script in registry
        assert "from_page_module" in registry[script]
        assert "from_page_module" not in registry.get(Path(__file__), {})


class TestLayoutIntegration:
    """``Page`` composing page bodies through the ``layout.djx`` chain."""

    def test_create_url_pattern_with_layout(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """A pattern built before any render still renders through the ancestor layout."""
        layout_file = tmp_path / "layout.djx"
        layout_content = "<html><body>{% template %}</body></html>"
        layout_file.write_text(layout_content)

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>{{ title }}</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"
        pattern = page_instance.create_url_pattern(
            "test", page_file, url_parser, page_view
        )

        assert pattern is not None
        result = page_instance.render(page_file, url_kwargs={"title": "Test"})
        assert "Test" in result

    def test_render_with_layout_inheritance(self, page_instance, tmp_path) -> None:
        """`Page.render` nests a sibling layout inside its ancestor layout."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "layout.djx").write_text("<main>{% template %}</main>")
        (sub_dir / "template.djx").write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance.render(page_file, url_kwargs={"title": "Test"})

        assert result.startswith("<html><body><main>")
        assert "<h1>Test</h1>" in result
        assert result.endswith("</main></body></html>")
        assert "{% template %}" not in result

    def test_render_composes_template_djx_under_ancestor_layout(
        self, page_instance, tmp_path
    ) -> None:
        """Page.render wraps the sibling template.djx body through ancestor layouts."""
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("<html><body>{% template %}</body></html>")

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance.render(page_file, url_kwargs={"title": "Hi"})

        assert "<html><body>" in result
        assert "<h1>Hi</h1>" in result
        assert "</body></html>" in result
        assert page_file in page_instance._templates.composed

    def test_render_with_layout_template_detection(
        self, page_instance, tmp_path
    ) -> None:
        """A body with no layout on disk renders verbatim, nothing wrapped around it."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        result = page_instance.render(page_file, url_kwargs={"title": "Test"})

        assert result == "<h1>Test</h1>"


class TestLoadPythonModule:
    """Loading a ``page.py`` module from a filesystem path."""

    def test_load_python_module_invalid_file(self, tmp_path) -> None:
        """A file that fails to parse raises, and the memo is what records it."""
        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("invalid python syntax {")

        with pytest.raises(SyntaxError, match="never closed"):
            _load_python_module(invalid_file)

    def test_load_python_module_nonexistent_file(self, tmp_path) -> None:
        """A path that does not exist raises, since the memo never hands it over."""
        nonexistent_file = tmp_path / "nonexistent.py"

        with pytest.raises(FileNotFoundError, match=r"nonexistent\.py"):
            _load_python_module(nonexistent_file)

    def test_load_python_module_no_spec_returns_none(self, tmp_path) -> None:
        """A path importlib cannot build a spec for yields ``None``."""
        valid_file = tmp_path / "page.py"
        valid_file.write_text("x = 1")
        with patch("importlib.util.spec_from_file_location", return_value=None):
            result = _load_python_module(valid_file)
        assert result is None

    def test_load_python_module_valid_file_returns_module(self, tmp_path) -> None:
        """A valid module is imported and handed back with its attributes bound."""
        valid_file = tmp_path / "page.py"
        valid_file.write_text("x = 42\ntemplate = '<p>{{ x }}</p>'")

        result = _load_python_module(valid_file)
        assert result is not None
        assert hasattr(result, "x")
        assert result.x == 42
        assert hasattr(result, "template")


class TestBrokenPageImportView:
    """The routed view surfaces a recorded import failure per request."""

    def _broken_pattern(
        self, page_instance, tmp_path, url_parser, source: str = _BROKEN_SYNTAX
    ):
        page_dir = tmp_path / "sub"
        page_dir.mkdir(exist_ok=True)
        page_file = page_dir / "page.py"
        page_file.write_text(source)
        pattern = page_instance.create_url_pattern(
            "broken", page_file, url_parser, page_view
        )
        assert pattern is not None
        return page_file, pattern

    def test_broken_page_sibling_still_renders(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """One broken page.py never takes down its sibling's pattern or render."""
        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        broken_file = broken_dir / "page.py"
        broken_file.write_text("def render( invalid syntax {\n")

        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        ok_file = ok_dir / "page.py"
        ok_file.write_text('template = "<p>ok body</p>"\n')

        broken_pattern = page_instance.create_url_pattern(
            "broken", broken_file, url_parser, page_view
        )
        ok_pattern = page_instance.create_url_pattern(
            "ok", ok_file, url_parser, page_view
        )

        assert broken_pattern is not None
        assert ok_pattern is not None
        response = ok_pattern.callback(build_page_request())
        assert response.status_code == 200
        assert b"<p>ok body</p>" in response.content

    @_broken_sources
    def test_broken_page_raises_under_debug(
        self, page_instance, tmp_path, url_parser, broken_source
    ) -> None:
        _page_file, pattern = self._broken_pattern(
            page_instance, tmp_path, url_parser, broken_source
        )
        with (
            override_settings(DEBUG=True),
            pytest.raises(PageModuleImportError, match="failed to import"),
        ):
            pattern.callback(build_page_request())

    def test_broken_page_traceback_does_not_grow_across_requests(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        # Re-raising a stored instance grows its traceback and pins request frames.
        _page_file, pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        with override_settings(DEBUG=True):
            with pytest.raises(PageModuleImportError) as first:
                pattern.callback(build_page_request())
            with pytest.raises(PageModuleImportError) as second:
                pattern.callback(build_page_request())

        assert first.value is not second.value
        first_depth = len(traceback.extract_tb(first.value.__traceback__))
        second_depth = len(traceback.extract_tb(second.value.__traceback__))
        assert first_depth == second_depth

    def test_healthy_page_renders_under_debug_with_sibling_error_recorded(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        _page_file, _pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        ok_file = ok_dir / "page.py"
        ok_file.write_text('template = "<p>ok body</p>"\n')
        ok_pattern = page_instance.create_url_pattern(
            "ok", ok_file, url_parser, page_view
        )
        assert ok_pattern is not None

        with override_settings(DEBUG=True):
            response = ok_pattern.callback(build_page_request())

        assert response.status_code == 200
        assert b"<p>ok body</p>" in response.content

    @_broken_sources
    def test_broken_page_raises_under_strict_loading(
        self, page_instance, tmp_path, url_parser, broken_source
    ) -> None:
        _page_file, pattern = self._broken_pattern(
            page_instance, tmp_path, url_parser, broken_source
        )
        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}),
            pytest.raises(PageModuleImportError, match="failed to import"),
        ):
            pattern.callback(build_page_request())

    @_broken_sources
    def test_broken_page_raises_in_prod(
        self, page_instance, tmp_path, url_parser, broken_source
    ) -> None:
        """With both flags off the broken page raises, never a 404 or a sibling body."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        _page_file, pattern = self._broken_pattern(
            page_instance, tmp_path, url_parser, broken_source
        )

        with pytest.raises(PageModuleImportError, match="failed to import"):
            pattern.callback(build_page_request())

    def test_broken_page_recovers_after_fix_without_restart(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        page_file, pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        with pytest.raises(PageModuleImportError, match="failed to import"):
            pattern.callback(build_page_request())

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text('template = "<p>revived</p>"\n')
        os.utime(page_file, (stamp, stamp))

        response = pattern.callback(build_page_request())
        assert response.status_code == 200
        assert b"<p>revived</p>" in response.content

    @pytest.mark.parametrize(
        "fixed",
        [
            "from next.pages import page\n",
            "from next.pages import page\n\npage.login_required()\n",
            "from next.pages import page\n\npage.login_required(inherit=True)\n",
        ],
        ids=["bare", "own", "inherit"],
    )
    def test_a_fix_without_a_body_answers_404(
        self, page_instance, tmp_path, url_parser, fixed
    ) -> None:
        (tmp_path / "layout.djx").write_text("<main>{% template %}</main>")
        page_file, pattern = self._broken_pattern(page_instance, tmp_path, url_parser)

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text(fixed)
        os.utime(page_file, (stamp, stamp))

        with pytest.raises(Http404):
            pattern.callback(build_page_request())

    def test_page_broken_after_build_raises_under_debug(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text('template = "<p>ok</p>"\n')
        pattern = page_instance.create_url_pattern(
            "later", page_file, url_parser, page_view
        )
        assert pattern is not None

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text(_BROKEN_SYNTAX)
        os.utime(page_file, (stamp, stamp))
        # Another subsystem's reload records the failure, as static discovery would.
        assert load_python_module_memo(page_file) is None

        with (
            override_settings(DEBUG=True),
            pytest.raises(PageModuleImportError, match="failed to import"),
        ):
            pattern.callback(build_page_request())

    def test_render_page_broken_after_build_raises_under_debug(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        # The build-time module survives the rewrite, only the record reports it.
        page_dir = tmp_path / "dyn"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("def render(request):\n    return '<p>ok</p>'\n")
        pattern = page_instance.create_url_pattern(
            "dyn", page_file, url_parser, page_view
        )
        assert pattern is not None

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text(_BROKEN_SYNTAX)
        os.utime(page_file, (stamp, stamp))
        assert load_python_module_memo(page_file) is None

        with (
            override_settings(DEBUG=True),
            pytest.raises(PageModuleImportError, match="failed to import"),
        ):
            pattern.callback(build_page_request())

    def test_render_page_renders_under_debug_with_sibling_error_recorded(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        _page_file, _pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        ok_dir = tmp_path / "dyn"
        ok_dir.mkdir()
        ok_file = ok_dir / "page.py"
        ok_file.write_text("def render(request):\n    return '<p>dyn body</p>'\n")
        ok_pattern = page_instance.create_url_pattern(
            "dyn", ok_file, url_parser, page_view
        )
        assert ok_pattern is not None

        with override_settings(DEBUG=True):
            response = ok_pattern.callback(build_page_request())

        assert response.status_code == 200
        assert b"<p>dyn body</p>" in response.content

    def test_broken_page_requests_do_not_reexec_module(
        self, page_instance, tmp_path, url_parser, monkeypatch
    ) -> None:
        real_load = loaders_module._load_python_module
        calls: list[Path] = []

        def counting(file_path: Path) -> object:
            calls.append(file_path)
            return real_load(file_path)

        monkeypatch.setattr(loaders_module, "_load_python_module", counting)

        page_file, pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        # The build probes once, and every later request raises from the memo.
        with pytest.raises(PageModuleImportError, match="failed to import"):
            pattern.callback(build_page_request())
        first_pass = calls.count(page_file)
        with pytest.raises(PageModuleImportError, match="failed to import"):
            pattern.callback(build_page_request())

        assert calls.count(page_file) == first_pass


class TestLayoutComposeBody:
    """`LayoutTemplateLoader.compose_body` is a pure string → string wrap."""

    def test_no_layouts_returns_body_verbatim(self, tmp_path) -> None:
        """Without layout.djx the body is returned unchanged."""
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        loader = LayoutTemplateLoader()
        assert loader.compose_body("<p>hi</p>", page_file) == "<p>hi</p>"

    def test_ancestor_layout_wraps_body_in_placeholder(self, tmp_path) -> None:
        """Without a sibling layout the body becomes a paired placeholder fallback."""
        (tmp_path / "layout.djx").write_text("<main>{% template %}</main>")
        sub = tmp_path / "sub"
        sub.mkdir()
        page_file = sub / "page.py"
        loader = LayoutTemplateLoader()
        result = loader.compose_body("<p>body</p>", page_file)
        assert result == "<main>{% #template %}<p>body</p>{% /template %}</main>"

    def test_sibling_layout_substitutes_body_directly(self, tmp_path) -> None:
        """With a sibling layout the body replaces the placeholder verbatim."""
        (tmp_path / "layout.djx").write_text("<section>{% template %}</section>")
        page_file = tmp_path / "page.py"
        loader = LayoutTemplateLoader()
        result = loader.compose_body("<p>body</p>", page_file)
        assert result == "<section><p>body</p></section>"


class _MdLoader(TemplateLoader):
    """Test-only loader wrapping a sibling `template.md` in an `<article>`."""

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
        with override_settings(
            NEXT_FRAMEWORK={"TEMPLATE_LOADERS": ["tests.pages.test_manager._MdLoader"]}
        ):
            build_registered_loaders.cache_clear()
            page.clear_template_caches()
            yield
        build_registered_loaders.cache_clear()
        page.clear_template_caches()

    def test_custom_loader_body_is_rendered_through_layout(
        self, page_instance, tmp_path
    ) -> None:
        """A custom loader for `template.md` feeds `_load_static_body`."""
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
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

        module = load_python_module_memo(page_file)
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
        paths = page_instance._templates.source_paths(page_file)
        assert md in paths


class TestUnifiedViewRendersTheHead:
    """A layout's ``{% metadata %}`` renders the folded chain of the page it wraps."""

    def test_the_head_carries_the_page_metadata(self, html: str) -> None:
        assert html.startswith("<html><head><title>Headed</title>\n")
        assert "<h1>headed page</h1>" in html

    def test_the_description_is_escaped(self, html: str) -> None:
        assert "<script>alert(1)</script>" not in html
        assert 'content="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">' in html

    def test_jsonld_never_closes_the_script(self, html: str) -> None:
        assert "</script><!--" not in html
        assert (
            '<script type="application/ld+json">{"@context": "https://schema.org", '
            '"@graph": [{"@type": "WebPage", "name": "\\u003C/script\\u003E\\u003C!--"}]}'
            "</script>" in html
        )

    def test_og_is_derived_from_the_page(self, html: str) -> None:
        assert '<meta property="og:title" content="Headed">' in html
        assert '<meta property="og:url" content="https://acme.example/headed/">' in html
        assert '<meta property="og:type" content="website">' in html

    def test_the_canonical_is_the_self_url_under_the_base(self) -> None:
        assert '<link rel="canonical" href="https://acme.example/headed/">' in (
            _render("?utm=1&page=1")
        )

    @override_settings(NEXT_FRAMEWORK=PAGED)
    def test_every_render_resolves_the_canonical_of_its_own_query(self) -> None:
        second = _render("?utm=1&page=2")
        third = _render("?page=3")
        assert '<link rel="canonical" href="https://acme.example/headed/?page=2">' in (
            second
        )
        assert '<link rel="canonical" href="https://acme.example/headed/?page=3">' in (
            third
        )


class TestTheHeadThroughARequest:
    """A request renders the head of the current page tree, braces in values literal."""

    @override_settings(DEBUG=True)
    def test_an_edited_ancestor_reaches_the_next_request_under_debug(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "pages"
        ancestor = write_page(root, "", ROOT, body=HEAD)
        write_page(root, "leaf", 'metadata = {"title": "Leaf"}\n', body=HEAD)
        with routed(root):
            first = Client().get("/leaf/")
            touch_later(ancestor, ROOT.replace("Root", "Edited"))
            second = Client().get("/leaf/")
        assert_metadata(first, title="Leaf | Root", description="Root")
        assert_metadata(second, title="Leaf | Edited", description="Edited")

    def test_braces_in_a_title_and_a_site_name_stay_literal(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "pages"
        write_page(
            root,
            "",
            'metadata = {"title": {"template": "{title} | {site_name}"}, '
            '"site_name": "Acme {Co}"}\n',
            body=HEAD,
        )
        write_page(
            root, "deals", 'metadata = {"title": "Deals {50%} {title}"}\n', body=HEAD
        )
        with routed(root):
            response = Client().get("/deals/")
        assert response.status_code == 200
        assert_metadata(response, title="Deals {50%} {title} | Acme {Co}")


class TestStaticFastPathView:
    """A page without ``render()`` serves the cached composed template."""

    def test_warm_get_opens_no_template_file(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The second GET reads nothing off disk and reuses the compiled template."""
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)

        view(build_page_request(), title="One")
        compiled = page_instance._templates.compiled[page_file]
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))
        view(build_page_request(), title="Two")

        assert reads == []
        assert page_instance._templates.compiled[page_file] is compiled

    def test_html_matches_the_body_resolution_path(
        self, page_instance, tmp_path
    ) -> None:
        """The fast path composes the same HTML the per-request path composes."""
        page_file = build_nested_page(tmp_path)
        module = load_python_module_memo(page_file)
        view = unified_view(page_instance, page_file)
        request = build_page_request()

        fast = view(request, title="Hi").content.decode()

        reference = Page()
        body = reference._load_static_body(page_file, module)
        slow = reference.render_composed(
            page_file, body, request, {"title": "Hi"}, dep_cache={}
        )
        assert fast == slow
        assert fast == "<html><main><h1>Hi</h1></main></html>"

    def test_created_layout_shows_up_without_a_restart(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A ``layout.djx`` created next to the page wraps the next GET."""
        page_file = build_nested_page(tmp_path, body="<h1>body</h1>")
        view = unified_view(page_instance, page_file)
        assert (
            view(build_page_request()).content
            == b"<html><main><h1>body</h1></main></html>"
        )

        (page_file.parent / "layout.djx").write_text(
            "<article>{% template %}</article>"
        )
        touch_later(page_file.parent)

        assert b"<article>" in view(build_page_request()).content

    def test_deleted_layout_disappears_without_a_restart(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """An ancestor ``layout.djx`` deleted on disk leaves the next composition."""
        page_file = build_nested_page(tmp_path, body="<h1>body</h1>")
        view = unified_view(page_instance, page_file)
        assert b"<main>" in view(build_page_request()).content

        (page_file.parent.parent / "layout.djx").unlink()

        assert view(build_page_request()).content == b"<html><h1>body</h1></html>"

    def test_recompose_reads_the_current_template_attribute(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A recomposed page reads `page.py` as it stands, not as it was built."""
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"
        page_file.write_text('template = "<p>first</p>"')
        view = unified_view(page_instance, page_file)
        assert b"first" in view(build_page_request()).content

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text('template = "<p>second</p>"')
        os.utime(page_file, (stamp, stamp))
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")

        assert b"second" in view(build_page_request()).content

    def test_template_loaded_fires_on_warm_up_only(
        self, page_instance, tmp_path, capture_template_loaded
    ) -> None:
        """The signal reports the composition, so a warm hit stays silent."""
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)

        view(build_page_request())
        assert [event.kwargs["file_path"] for event in capture_template_loaded] == [
            page_file
        ]

        view(build_page_request())
        assert len(capture_template_loaded) == 1


class TestBoundaryChainLayers:
    """The chain rides the layer it composed with, a lookup only without one."""

    def test_a_held_skeleton_without_a_chain_looks_it_up(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_instance = Page()
        page_instance._templates.skeleton[page_file] = "held"

        text, chain = page_instance.layout_skeleton(page_file)

        assert text == "held"
        assert chain is page_boundaries(page_file)

    def test_a_registered_template_drops_its_chain(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_instance = Page()
        page_instance._templates.composed_chains[page_file] = NO_BOUNDARIES

        page_instance.register_template(page_file, "<p>x</p>")

        assert page_file not in page_instance._templates.composed_chains
        assert page_instance.composed_chain(page_file) is page_boundaries(page_file)
