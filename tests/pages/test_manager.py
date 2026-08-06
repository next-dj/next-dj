import importlib.util
import os
import textwrap
import traceback
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from django.core.checks import Error
from django.http import Http404, HttpRequest
from django.template import Template
from django.test import override_settings

import next.pages.loaders as loaders_module
from next.checks import _load_python_module
from next.conf import next_framework_settings
from next.pages import Page, context, page
from next.pages.loaders import (
    LayoutTemplateLoader,
    PageModuleImportError,
    TemplateLoader,
    _load_python_module_memo,
)
from next.pages.manager import iter_serialized_page_context_keys
from next.pages.registry import PageContextRegistry
from next.static import default_manager as static_default_manager
from tests.support import (
    MalformedRootsRouter,
    attribution,
    handler_declared_here,
    patch_checks_router_manager,
)


class TestPage:
    """Registration, context collection, and rendering on a fresh ``Page``."""

    def test_init(self, page_instance) -> None:
        """A fresh ``Page`` starts with empty registries and its own layout loader."""
        assert page_instance._template_registry == {}
        assert isinstance(page_instance._context_manager, PageContextRegistry)
        assert isinstance(page_instance._layout_loader, LayoutTemplateLoader)

    def test_register_template_direct(self, page_instance) -> None:
        """``register_template`` stores the source under the exact path it was given."""
        file_path = Path("/test/path/page.py")
        template_str = "Hello {{ name }}!"

        page_instance.register_template(file_path, template_str)

        assert file_path in page_instance._template_registry
        assert page_instance._template_registry[file_path] == template_str

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
        """One call drops the source, the compiled template, and the mtimes."""
        file_path = Path("/test/path/page.py")
        page_instance.register_template(file_path, "<p>x</p>")
        page_instance._compiled_registry[file_path] = Template("<p>x</p>")
        page_instance._template_source_mtimes[file_path] = {}

        page_instance.clear_template_caches()

        assert page_instance._template_registry == {}
        assert page_instance._compiled_registry == {}
        assert page_instance._template_source_mtimes == {}

    @pytest.mark.parametrize(
        ("decorator_type", "expected_key"),
        [
            ("with_key", "user_name"),
            ("without_key", None),
            ("without_parentheses", None),
        ],
        ids=["with_key", "without_key", "without_parentheses"],
    )
    def test_context_decorator_variations(
        self, page_instance, decorator_type, expected_key
    ) -> None:
        """``@context`` keys on the declaring file whether or not a key is given."""
        if decorator_type == "with_key":

            @page_instance.context("user_name")
            def get_user_name() -> str:
                return "John Doe"

            func = get_user_name
        elif decorator_type == "without_key":

            @page_instance.context()
            def get_context_data():
                return {"key1": "value1", "key2": "value2"}

            func = get_context_data
        else:

            @page_instance.context
            def get_context_data_bare():
                return {"key1": "value1", "key2": "value2"}

            func = get_context_data_bare

        registry = page_instance._context_manager._context_registry
        assert Path(__file__) in registry
        assert expected_key in registry[Path(__file__)]
        entry = registry[Path(__file__)][expected_key]
        assert entry.func == func
        assert entry.inherit_context is False
        assert entry.serialize is False

    def test_context_decorator_with_inherit_context(self, page_instance) -> None:
        """A keyed ``@context`` records ``inherit_context`` on the registry entry."""

        @page_instance.context("inherited_key", inherit_context=True)
        def get_inherited_value() -> str:
            return "inherited_value"

        registry = page_instance._context_manager._context_registry
        assert Path(__file__) in registry
        assert "inherited_key" in registry[Path(__file__)]
        entry = registry[Path(__file__)]["inherited_key"]
        assert entry.func == get_inherited_value
        assert entry.inherit_context is True
        assert entry.serialize is False

    def test_context_decorator_without_key_inherit_context(self, page_instance) -> None:
        """A dict-merge ``@context`` registers under the ``None`` key and keeps inheritance."""

        @page_instance.context(inherit_context=True)
        def get_context_data():
            return {"key1": "value1", "key2": "value2"}

        registry = page_instance._context_manager._context_registry
        assert Path(__file__) in registry
        assert None in registry[Path(__file__)]
        entry = registry[Path(__file__)][None]
        assert entry.func == get_context_data
        assert entry.inherit_context is True
        assert entry.serialize is False

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
        """``render`` merges registered context functions with the caller keyword arguments."""
        page_instance.register_template(test_file_path, template_str)

        if context_setup:
            for key, func in context_setup.items():
                page_instance._context_manager.register_context(
                    test_file_path, key, func
                )

        result = page_instance.render(test_file_path, **render_kwargs)

        assert result == expected

    def test_render_with_multiple_files(self, page_instance) -> None:
        """Two page paths keep separate templates and context, with no cross-talk."""
        file1 = Path("/test/path/page1.py")
        template1 = "Page 1: {{ title }}"
        page_instance.register_template(file1, template1)
        page_instance._context_manager.register_context(
            file1, "title", lambda: "First Page"
        )

        file2 = Path("/test/path/page2.py")
        template2 = "Page 2: {{ title }}"
        page_instance.register_template(file2, template2)
        page_instance._context_manager.register_context(
            file2, "title", lambda: "Second Page"
        )

        result1 = page_instance.render(file1)
        result2 = page_instance.render(file2)

        assert result1 == "Page 1: First Page"
        assert result2 == "Page 2: Second Page"

    def test_render_with_inherited_context(self, page_instance, tmp_path) -> None:
        """A child page reads a parent ``page.py`` value marked ``inherit_context``."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        template_str = "Child page: {{ inherited_var }}"
        page_instance.register_template(child_page_file, template_str)

        def layout_func() -> str:
            return "inherited_value"

        page_instance._context_manager.register_context(
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
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        template_str = "Child page: {{ var }}"
        page_instance.register_template(child_page_file, template_str)

        def layout_func() -> str:
            return "layout_value"

        page_instance._context_manager.register_context(
            page_file, "var", layout_func, inherit_context=True
        )

        def child_func() -> str:
            return "child_value"

        page_instance._context_manager.register_context(
            child_page_file, "var", child_func, inherit_context=False
        )

        result = page_instance.render(child_page_file)

        assert "Child page: child_value" in result

    def test_context_registry_defaultdict_behavior(
        self, page_instance, test_file_path
    ) -> None:
        """Registering a key creates the per-file entry without pre-seeding it."""
        page_instance._context_manager.register_context(
            test_file_path, "test_key", lambda: "test_value"
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

    def test_render_with_no_body_source_returns_empty_block(
        self, page_instance, tmp_path
    ) -> None:
        """Page.render returns an empty `{% block template %}` slot when no source exists."""
        page_file = tmp_path / "page.py"
        page_file.write_text("y = 1")
        result = page_instance.render(page_file)
        assert result == ""

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


class TestComposedTemplateCache:
    """`composed_template_for` caches the compiled composed template by mtime."""

    def test_render_twice_reuses_compiled_template(
        self, page_instance, tmp_path
    ) -> None:
        """A warm render reuses the compiled Template object as-is."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<h1>{{ title }}</h1>")
        page_instance.render(page_file, title="One")
        compiled = page_instance._compiled_registry[page_file]
        result = page_instance.render(page_file, title="Two")
        assert page_instance._compiled_registry[page_file] is compiled
        assert "<h1>Two</h1>" in result

    def test_stale_source_recompiles(self, page_instance, tmp_path) -> None:
        """An edited template.djx invalidates both the source and compiled caches."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        djx = tmp_path / "template.djx"
        djx.write_text("<h1>{{ title }}</h1>")
        page_instance.render(page_file, title="One")
        compiled = page_instance._compiled_registry[page_file]
        djx.write_text("<h2>{{ title }}</h2>")
        result = page_instance.render(page_file, title="Two")
        assert page_instance._compiled_registry[page_file] is not compiled
        assert "<h2>Two</h2>" in result

    def test_stale_layout_recompiles(self, page_instance, tmp_path) -> None:
        """An edited ancestor layout.djx invalidates the compiled cache too."""
        layout = tmp_path / "layout.djx"
        layout.write_text("<html>{% block template %}{% endblock template %}</html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("x = 1")
        (page_dir / "template.djx").write_text("<p>body</p>")
        assert "<html>" in page_instance.render(page_file)
        layout.write_text("<main>{% block template %}{% endblock template %}</main>")
        assert "<main>" in page_instance.render(page_file)

    def test_register_template_drops_compiled_entry(
        self, page_instance, tmp_path
    ) -> None:
        """Every source-registry write evicts the compiled entry alongside."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<h1>old</h1>")
        page_instance.render(page_file)
        assert page_file in page_instance._compiled_registry
        page_instance.register_template(page_file, "<p>replaced</p>")
        assert page_file not in page_instance._compiled_registry
        template = page_instance.composed_template_for(page_file)
        assert template.source == "<p>replaced</p>"

    def test_composed_template_carries_page_origin(
        self, page_instance, tmp_path
    ) -> None:
        """The compiled composed template names the page path as its origin."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<p>ok</p>")
        template = page_instance.composed_template_for(page_file)
        assert template.origin.name == str(page_file)
        assert template.name == str(page_file)

    def test_render_function_pages_bypass_compiled_cache(
        self, page_instance, tmp_path
    ) -> None:
        """Dynamic `render()` bodies never populate the compiled cache."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    return '<p>dynamic</p>'\n"
        )
        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert b"dynamic" in response.content
        assert page_file not in page_instance._compiled_registry


class TestGlobalPageInstance:
    """The module-level ``page`` singleton and its ``context`` alias."""

    @pytest.fixture(autouse=True)
    def clear_global_state(self):
        """Give each test a clean global page state, then restore the baseline.

        The global `page` singleton holds the context providers every page
        registered at URL-conf build time. A bare clear would strip those
        from whatever worker runs this class under xdist, so a later page
        render on the same worker would find no providers. Snapshotting and
        restoring keeps the suite order-independent.
        """
        template_snapshot = dict(page._template_registry)
        context_snapshot = {
            path: dict(entries)
            for path, entries in page._context_manager._context_registry.items()
        }
        page._template_registry.clear()
        page._context_manager._context_registry.clear()
        yield
        page._template_registry.clear()
        page._template_registry.update(template_snapshot)
        page._context_manager._context_registry.clear()
        page._context_manager._context_registry.update(context_snapshot)

    def test_global_page_instance(self) -> None:
        """The exported ``page`` is a ``Page`` carrying the same registries."""
        assert page is not None
        assert isinstance(page, Page)
        assert page._template_registry == {}
        assert page._context_manager._context_registry == {}

    def test_context_alias(self) -> None:
        """The exported ``context`` is the singleton's own bound decorator."""
        assert context == page.context

    def test_global_page_template_registration(self, global_file_path) -> None:
        """A template registered on the singleton lands in its registry."""
        template_str = "Global template: {{ message }}"
        page.register_template(global_file_path, template_str)

        assert global_file_path in page._template_registry
        assert page._template_registry[global_file_path] == template_str

    def test_global_page_context_registration(self) -> None:
        """A context function registered on the singleton lands in its registry."""

        @page.context("global_key")
        def get_global_value() -> str:
            return "global_value"

        registry = page._context_manager._context_registry
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

        registry = page._context_manager._context_registry
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

        registry = page._context_manager._context_registry
        assert script in registry
        assert "from_page_module" in registry[script]
        assert "from_page_module" not in registry.get(Path(__file__), {})


class TestContextMisattribution:
    """A decorator run on a callable declared elsewhere is recorded as such."""

    def test_declaring_file_matching_the_caller_records_nothing(self) -> None:
        """A callable declared where the decorator runs is attributed cleanly."""
        instance = Page()

        @instance.context("here")
        def declared_here() -> str:
            return "value"

        assert instance._context_manager.misattributed() == ()

    def test_helper_from_another_module_is_recorded_once(self) -> None:
        """Decorating an imported helper records the pair of files it spans."""
        instance = Page()

        instance.context("greeting")(handler_declared_here)
        instance.context("greeting_again")(handler_declared_here)

        records = instance._context_manager.misattributed()
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (Path(__file__), Path(attribution.__file__), "handler_declared_here")
        ]

    def test_reset_drops_recorded_misattributions(self) -> None:
        """A registry reset clears the diagnostic along with the registrations."""
        instance = Page()
        instance.context("greeting")(handler_declared_here)

        instance._context_manager.reset()

        assert instance._context_manager.misattributed() == ()


class TestLayoutIntegration:
    """``Page`` composing page bodies through the ``layout.djx`` chain."""

    def test_create_url_pattern_with_layout(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """A pattern built before any render still renders through the ancestor layout."""
        layout_file = tmp_path / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>{{ title }}</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"
        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        result = page_instance.render(page_file, title="Test")
        assert "Test" in result

    def test_render_with_layout_inheritance(self, page_instance, tmp_path) -> None:
        """`Page.render` nests a sibling layout inside its ancestor layout."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "layout.djx").write_text(
            "<main>{% block template %}{% endblock template %}</main>"
        )
        (sub_dir / "template.djx").write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance.render(page_file, title="Test")

        assert result.startswith("<html><body><main>")
        assert "<h1>Test</h1>" in result
        assert result.endswith("</main></body></html>")
        assert "{% block template %}" not in result

    def test_render_composes_template_djx_under_ancestor_layout(
        self, page_instance, tmp_path
    ) -> None:
        """Page.render wraps the sibling template.djx body through ancestor layouts."""
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_file.write_text("<h1>{{ title }}</h1>")

        page_file = sub_dir / "page.py"
        result = page_instance.render(page_file, title="Hi")

        assert "<html><body>" in result
        assert "<h1>Hi</h1>" in result
        assert "</body></html>" in result
        assert page_file in page_instance._template_registry

    def test_render_with_layout_template_detection(
        self, page_instance, tmp_path
    ) -> None:
        """A body with no layout on disk renders verbatim, with nothing wrapped around it."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        result = page_instance.render(page_file, title="Test")

        assert result == "<h1>Test</h1>"


class TestLoadPythonModule:
    """Loading a ``page.py`` module from a filesystem path."""

    def test_load_python_module_invalid_file(self, tmp_path) -> None:
        """A file that fails to parse yields ``None`` rather than raising."""
        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("invalid python syntax {")

        result = _load_python_module(invalid_file)
        assert result is None

    def test_load_python_module_nonexistent_file(self, tmp_path) -> None:
        """A path that does not exist yields ``None``."""
        nonexistent_file = tmp_path / "nonexistent.py"

        result = _load_python_module(nonexistent_file)
        assert result is None

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


def _make_real_request() -> HttpRequest:
    """Build a minimal `HttpRequest` usable by the unified view."""
    request = HttpRequest()
    request.method = "GET"
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    return request


class TestUnifiedViewBodyResolution:
    """`_create_unified_view` resolves the body via render > template > template.djx."""

    @pytest.fixture(autouse=True)
    def _isolate(self):
        page._template_registry.clear()
        page._template_source_mtimes.clear()
        yield
        page._template_registry.clear()
        page._template_source_mtimes.clear()

    def test_template_attribute_with_ancestor_layout_composes(
        self, page_instance, tmp_path
    ) -> None:
        """`template = "..."` flows through an ancestor `layout.djx`."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text('template = "<h1>attr body</h1>"')

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "<h1>attr body</h1>" in body
        assert "</body></html>" in body

    def test_render_returning_str_with_ancestor_layout_composes(
        self, page_instance, tmp_path
    ) -> None:
        """`render()` returning a string flows through the ancestor layout."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    return '<p>rendered</p>'\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "<p>rendered</p>" in body

    def test_render_returning_httpresponse_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`render()` returning HttpResponse is returned verbatim, no layout."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import HttpResponse\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponse('raw', status=201)\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert response.status_code == 201
        assert response.content == b"raw"
        assert "<html>" not in response.content.decode()

    def test_render_returning_redirect_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`HttpResponseRedirect` (an HttpResponse subclass) is returned verbatim."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import HttpResponseRedirect\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponseRedirect('/target/')\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert response.status_code == 302
        assert response["Location"] == "/target/"

    def test_render_returning_jsonresponse_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`JsonResponse` (an HttpResponse subclass) is returned verbatim."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import JsonResponse\n"
            "def render(request, **kwargs):\n"
            "    return JsonResponse({'ok': True})\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert response["Content-Type"].startswith("application/json")
        assert response.content == b'{"ok": true}'

    @pytest.mark.parametrize(
        "return_value",
        ["None", "{'x': 1}", "[1, 2]", "42"],
        ids=["None", "dict", "list", "int"],
    )
    def test_render_returning_non_str_non_response_raises(
        self, page_instance, tmp_path, return_value
    ) -> None:
        """`render()` returning anything other than str/HttpResponse raises TypeError."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            f"def render(request, **kwargs):\n    return {return_value}\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        with pytest.raises(TypeError, match="must return str or HttpResponse"):
            view(_make_real_request())

    def test_render_raising_propagates(self, page_instance, tmp_path) -> None:
        """`render()` raising an exception propagates to the caller."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    raise RuntimeError('boom')\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        with pytest.raises(RuntimeError, match="boom"):
            view(_make_real_request())

    def test_priority_render_wins_over_template_attr(
        self, page_instance, tmp_path
    ) -> None:
        """When both render() and template attr exist, render() wins."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            'template = "<p>from-attr</p>"\n'
            "def render(request, **kwargs):\n"
            "    return '<p>from-render</p>'\n"
        )

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert b"from-render" in response.content
        assert b"from-attr" not in response.content

    def test_priority_template_attr_wins_over_template_djx(
        self, page_instance, tmp_path
    ) -> None:
        """When both template attr and template.djx exist, attr wins."""
        (tmp_path / "template.djx").write_text("<p>from-djx</p>")
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "<p>from-attr</p>"')

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        assert b"from-attr" in response.content
        assert b"from-djx" not in response.content

    def test_empty_body_with_layout_renders_layout_shell(
        self, page_instance, tmp_path
    ) -> None:
        """A page with no body source still renders the ancestor layout's shell."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("")

        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_real_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "</body></html>" in body


_BROKEN_SYNTAX = "def render( invalid syntax {\n"
_BROKEN_IMPORT = "import missing_dep_xyz\n"

_broken_sources = pytest.mark.parametrize(
    "broken_source",
    [_BROKEN_SYNTAX, _BROKEN_IMPORT],
    ids=["syntax_error", "import_error"],
)


class TestBrokenPageImportView:
    """The routed view surfaces a recorded import failure per request."""

    def _broken_pattern(
        self, page_instance, tmp_path, url_parser, source: str = _BROKEN_SYNTAX
    ):
        page_dir = tmp_path / "sub"
        page_dir.mkdir(exist_ok=True)
        page_file = page_dir / "page.py"
        page_file.write_text(source)
        pattern = page_instance.create_url_pattern("broken", page_file, url_parser)
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
            "broken", broken_file, url_parser
        )
        ok_pattern = page_instance.create_url_pattern("ok", ok_file, url_parser)

        assert broken_pattern is not None
        assert ok_pattern is not None
        response = ok_pattern.callback(_make_real_request())
        assert response.status_code == 200
        assert b"<p>ok body</p>" in response.content

    @_broken_sources
    def test_broken_page_raises_under_debug(
        self, page_instance, tmp_path, url_parser, broken_source
    ) -> None:
        _page_file, pattern = self._broken_pattern(
            page_instance, tmp_path, url_parser, broken_source
        )
        with override_settings(DEBUG=True), pytest.raises(PageModuleImportError):
            pattern.callback(_make_real_request())

    def test_broken_page_traceback_does_not_grow_across_requests(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        # Re-raising one stored instance would append the raise chain to its
        # traceback on every request and pin request frame locals alive.
        _page_file, pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        with override_settings(DEBUG=True):
            with pytest.raises(PageModuleImportError) as first:
                pattern.callback(_make_real_request())
            with pytest.raises(PageModuleImportError) as second:
                pattern.callback(_make_real_request())

        assert first.value is not second.value
        first_depth = len(traceback.extract_tb(first.value.__traceback__))
        second_depth = len(traceback.extract_tb(second.value.__traceback__))
        assert first_depth == second_depth

    def test_healthy_page_renders_under_debug_with_sibling_error_recorded(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        # A recorded failure for another page must not trip the error probe
        # of a healthy view even when the volume flags are active.
        _page_file, _pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        ok_file = ok_dir / "page.py"
        ok_file.write_text('template = "<p>ok body</p>"\n')
        ok_pattern = page_instance.create_url_pattern("ok", ok_file, url_parser)
        assert ok_pattern is not None

        with override_settings(DEBUG=True):
            response = ok_pattern.callback(_make_real_request())

        assert response.status_code == 200
        assert b"<p>ok body</p>" in response.content

    @_broken_sources
    def test_broken_page_raises_under_strict_loading(
        self, page_instance, tmp_path, url_parser, broken_source
    ) -> None:
        _page_file, pattern = self._broken_pattern(
            page_instance, tmp_path, url_parser, broken_source
        )
        with override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}):
            next_framework_settings.reload()
            with pytest.raises(PageModuleImportError):
                pattern.callback(_make_real_request())

    @_broken_sources
    def test_broken_page_returns_404_in_prod(
        self, page_instance, tmp_path, url_parser, broken_source
    ) -> None:
        """With both flags off the broken page answers 404, never a sibling body."""
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        _page_file, pattern = self._broken_pattern(
            page_instance, tmp_path, url_parser, broken_source
        )

        with pytest.raises(Http404):
            pattern.callback(_make_real_request())

    def test_broken_page_recovers_after_fix_without_restart(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        page_file, pattern = self._broken_pattern(page_instance, tmp_path, url_parser)
        with pytest.raises(Http404):
            pattern.callback(_make_real_request())

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text('template = "<p>revived</p>"\n')
        os.utime(page_file, (stamp, stamp))

        response = pattern.callback(_make_real_request())
        assert response.status_code == 200
        assert b"<p>revived</p>" in response.content

    def test_page_broken_after_build_raises_under_debug(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text('template = "<p>ok</p>"\n')
        pattern = page_instance.create_url_pattern("later", page_file, url_parser)
        assert pattern is not None

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text(_BROKEN_SYNTAX)
        os.utime(page_file, (stamp, stamp))
        # Another subsystem's reload records the failure, as static
        # discovery or a checks pass would on a real deployment.
        assert _load_python_module_memo(page_file) is None

        with override_settings(DEBUG=True), pytest.raises(PageModuleImportError):
            pattern.callback(_make_real_request())

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
        # The pattern build probes once and every later request answers 404
        # off the memo, re-executing nothing.
        with pytest.raises(Http404):
            pattern.callback(_make_real_request())
        first_pass = calls.count(page_file)
        with pytest.raises(Http404):
            pattern.callback(_make_real_request())

        assert calls.count(page_file) == first_pass


class TestAuthorizationOutcomeBrokenPage:
    """`authorization_outcome` applies the view's guard to out-of-band morphs."""

    def _broken_file(self, tmp_path) -> Path:
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(_BROKEN_SYNTAX)
        return page_file

    def test_raises_under_debug(self, page_instance, tmp_path) -> None:
        page_file = self._broken_file(tmp_path)
        with override_settings(DEBUG=True), pytest.raises(PageModuleImportError):
            page_instance.authorization_outcome(page_file, _make_real_request())

    def test_raises_under_strict_loading(self, page_instance, tmp_path) -> None:
        page_file = self._broken_file(tmp_path)
        with override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}):
            next_framework_settings.reload()
            with pytest.raises(PageModuleImportError):
                page_instance.authorization_outcome(page_file, _make_real_request())

    def test_raises_in_prod_instead_of_404(self, page_instance, tmp_path) -> None:
        page_file = self._broken_file(tmp_path)
        with pytest.raises(PageModuleImportError):
            page_instance.authorization_outcome(page_file, _make_real_request())


class TestLoadStaticBodyEdgeCases:
    """`Page._load_static_body` edge cases."""

    def test_unreadable_template_djx_returns_empty(
        self, page_instance, tmp_path
    ) -> None:
        """UnicodeDecodeError on `template.djx` yields an empty body, not a crash."""
        template_djx = tmp_path / "template.djx"
        template_djx.write_bytes(b"\xff\xfe invalid utf-8")
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        assert page_instance._load_static_body(page_file, None) == ""

    def test_has_template_returns_true_when_ancestor_layout_exists(
        self, page_instance, tmp_path
    ) -> None:
        """`has_template` short-circuits to True when an ancestor layout applies."""
        (tmp_path / "layout.djx").write_text(
            "<main>{% block template %}{% endblock template %}</main>"
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        page_file = sub / "page.py"
        page_file.write_text("")
        assert page_instance.has_template(page_file, module=None) is True


class TestLayoutComposeBody:
    """`LayoutTemplateLoader.compose_body` is a pure string → string wrap."""

    def test_no_layouts_returns_body_verbatim(self, tmp_path) -> None:
        """Without layout.djx the body is returned unchanged."""
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        loader = LayoutTemplateLoader()
        assert loader.compose_body("<p>hi</p>", page_file) == "<p>hi</p>"

    def test_ancestor_layout_wraps_body_in_block(self, tmp_path) -> None:
        """Without a sibling layout the body is wrapped in a `{% block template %}`."""
        (tmp_path / "layout.djx").write_text(
            "<main>{% block template %}{% endblock template %}</main>"
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        page_file = sub / "page.py"
        loader = LayoutTemplateLoader()
        result = loader.compose_body("<p>body</p>", page_file)
        assert (
            result
            == "<main>{% block template %}<p>body</p>{% endblock template %}</main>"
        )

    def test_sibling_layout_substitutes_body_directly(self, tmp_path) -> None:
        """With a sibling layout the body replaces the placeholder verbatim."""
        (tmp_path / "layout.djx").write_text(
            "<section>{% block template %}{% endblock template %}</section>"
        )
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
        # the cache is a single-slot holder mutated in place, never rebound,
        # so a stale value on this worker cannot break the production reads
        loaders_module._REGISTERED_LOADERS_CACHE["value"] = [_MdLoader()]
        page._template_registry.clear()
        page._template_source_mtimes.clear()
        yield
        loaders_module._REGISTERED_LOADERS_CACHE["value"] = None
        page._template_registry.clear()
        page._template_source_mtimes.clear()

    def test_custom_loader_body_is_rendered_through_layout(
        self, page_instance, tmp_path
    ) -> None:
        """A custom loader for `template.md` feeds `_load_static_body`."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
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

        module = _load_python_module_memo(page_file)
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
        paths = page_instance._get_template_source_paths(page_file)
        assert md in paths


class TestSerializedPageContextKeys:
    """iter_serialized_page_context_keys reports what a page.py declares."""

    def _write_page(self, tmp_path: Path, body: str) -> Path:
        page_file = tmp_path / "page.py"
        page_file.write_text(textwrap.dedent(body))
        loaders_module._MODULE_MEMO.pop(page_file, None)
        return page_file

    def test_no_router_manager_reports_nothing(self) -> None:
        with patch(
            "next.pages.manager.get_router_manager", return_value=(None, [Error("x")])
        ):
            assert list(iter_serialized_page_context_keys()) == []

    def test_keyed_serialized_key_is_reported_once_per_page(self, tmp_path) -> None:
        page_file = self._write_page(
            tmp_path,
            """
            from next.pages import page


            @page.context("unread", serialize=True)
            def unread():
                return 3
            """,
        )
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch(
                "next.checks.common.walk_page_tree",
                return_value=[("first", page_file), ("second", page_file)],
            ),
        ):
            found = list(iter_serialized_page_context_keys())
        assert found == [(page_file, "unread")]

    def test_symlinked_spelling_of_one_page_reports_its_key_once(
        self, tmp_path
    ) -> None:
        real = tmp_path / "real"
        real.mkdir()
        page_file = self._write_page(
            real,
            """
            from next.pages import page


            @page.context("unread", serialize=True)
            def unread():
                return 3
            """,
        )
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        linked = tmp_path / "link" / "page.py"
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch(
                "next.checks.common.walk_page_tree",
                return_value=[("real", page_file), ("link", linked)],
            ),
        ):
            found = list(iter_serialized_page_context_keys())
        assert found == [(page_file, "unread")]

    def test_unserialized_and_keyless_contexts_are_skipped(self, tmp_path) -> None:
        self._write_page(
            tmp_path,
            """
            from next.pages import page


            @page.context("plain")
            def plain():
                return 1


            @page.context(serialize=True)
            def spread() -> dict:
                return {"$dev": True}
            """,
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert list(iter_serialized_page_context_keys()) == []

    def test_virtual_page_path_is_skipped(self, tmp_path) -> None:
        (tmp_path / "virtual").mkdir()
        (tmp_path / "virtual" / "template.djx").write_text("<p>ok</p>\n")

        with patch_checks_router_manager(pages_directory=tmp_path):
            assert list(iter_serialized_page_context_keys()) == []

    def test_unimportable_page_is_skipped(self, tmp_path) -> None:
        self._write_page(tmp_path, "def broken(:\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert list(iter_serialized_page_context_keys()) == []

    def test_a_router_reporting_the_wrong_tree_shape_is_skipped(self, tmp_path) -> None:
        # This runs on the render path, so a plugin handing back bare paths
        # may not turn a page render into an AttributeError.
        manager = MagicMock()
        manager.backends = (MalformedRootsRouter([tmp_path]),)
        with patch("next.pages.manager.get_router_manager", return_value=(manager, [])):
            assert list(iter_serialized_page_context_keys()) == []
