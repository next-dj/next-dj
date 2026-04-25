"""Tests for next.pages.registry."""

from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

from next.deps import DependencyResolver
from next.pages import Context, Page
from next.pages.context import ContextByDefaultProvider
from next.pages.registry import PageContextRegistry
from next.static import StaticCollector
from tests.support import inspect_parameter


class TestPageContextRegistry:
    """Test cases for PageContextRegistry."""

    def test_init(self, context_manager) -> None:
        """Test PageContextRegistry initialization."""
        assert context_manager._context_registry == {}

    def test_get_resolver_returns_injected_resolver(self) -> None:
        """When resolver is injected, _get_resolver() returns it."""
        r = DependencyResolver()
        cm = PageContextRegistry(resolver=r)
        assert cm._get_resolver() is r

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
        ids=["keyed", "dict_merge"],
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
            False,
        )

        result = context_manager.collect_context(test_file_path)
        assert result.context_data == expected_result
        assert result.js_context == {}

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

        assert result.context_data == {
            "key1": "value1",
            "key2": "value2",
            "key3": "value3",
        }

    def test_collect_context_second_function_gets_first_via_param_name(
        self, context_manager, test_file_path
    ) -> None:
        """Second @context("key") can access first key via Context()."""
        context_manager.register_context(
            test_file_path, "custom_context_var", lambda: "12345"
        )

        def landing(
            custom_context_var: str = Context(),
        ) -> dict[str, str]:
            return {"title": "Landing", "custom_context_var": custom_context_var}

        context_manager.register_context(test_file_path, "landing", landing)

        result = context_manager.collect_context(test_file_path)

        assert result.context_data["custom_context_var"] == "12345"
        assert result.context_data["landing"] == {
            "title": "Landing",
            "custom_context_var": "12345",
        }

    def test_collect_context_second_function_gets_value_by_param_name(
        self, context_manager, test_file_path
    ) -> None:
        """ContextByNameProvider injects context_data value when param name matches key."""
        context_manager.register_context(
            test_file_path, "by_name_key", lambda: "injected-by-name"
        )

        def use_key(by_name_key: str) -> dict[str, str]:
            return {"got": by_name_key}

        context_manager.register_context(test_file_path, "use_key", use_key)

        result = context_manager.collect_context(test_file_path)

        assert result.context_data["by_name_key"] == "injected-by-name"
        assert result.context_data["use_key"] == {"got": "injected-by-name"}

    def test_collect_context_no_functions(
        self, context_manager, test_file_path
    ) -> None:
        """Test collecting context when no functions are registered."""
        result = context_manager.collect_context(test_file_path)

        assert result.context_data == {}
        assert result.js_context == {}

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
        func, inherit, serialize = context_manager._context_registry[test_file_path][
            "inherited_key"
        ]
        assert func == test_func
        assert inherit is True
        assert serialize is False

    def test_collect_inherited_context(self, context_manager, tmp_path) -> None:
        """Test collecting inherited context from layout directories."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        def layout_func() -> str:
            return "layout_value"

        context_manager.register_context(
            page_file,
            "layout_var",
            layout_func,
            inherit_context=True,
        )

        result = context_manager.collect_context(child_page_file)

        assert "layout_var" in result.context_data
        assert result.context_data["layout_var"] == "layout_value"

    def test_collect_inherited_context_bounded_depth(
        self, context_manager, tmp_path
    ) -> None:
        """The ancestor walk is bounded by `_MAX_ANCESTOR_WALK_DEPTH`.

        This test fabricates a 70-level deep tree — past the 64 cap —
        and asserts the call returns in bounded time with an empty
        merged context rather than iterating all 70 ancestors.
        """
        deep = tmp_path
        for i in range(70):
            deep = deep / f"d{i}"
            deep.mkdir()
        leaf_page = deep / "page.py"

        result = context_manager.collect_context(leaf_page)
        assert result.context_data == {}

    def test_collect_inherited_context_multiple_levels(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting inherited context from multiple layout levels."""
        root_dir = tmp_path / "root"
        root_dir.mkdir()
        root_layout = root_dir / "layout.djx"
        root_layout.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )
        root_page = root_dir / "page.py"
        root_page.write_text("")

        sub_dir = root_dir / "sub"
        sub_dir.mkdir()
        sub_layout = sub_dir / "layout.djx"
        sub_layout.write_text("<div>{% block template %}{% endblock template %}</div>")
        sub_page = sub_dir / "page.py"
        sub_page.write_text("")

        child_dir = sub_dir / "child"
        child_dir.mkdir()
        child_page = child_dir / "page.py"

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

        result = context_manager.collect_context(child_page)

        assert "root_var" in result.context_data
        assert "sub_var" in result.context_data
        assert result.context_data["root_var"] == "root_value"
        assert result.context_data["sub_var"] == "sub_value"

    def test_collect_inherited_context_no_layout(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting context when no layout files exist."""
        page_file = tmp_path / "page.py"
        result = context_manager.collect_context(page_file)
        assert result.context_data == {}

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
        assert result.context_data == {}

    def test_collect_inherited_context_inherit_false(
        self, context_manager, tmp_path
    ) -> None:
        """Test that context with inherit_context=False is not inherited."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        def layout_func() -> str:
            return "layout_value"

        context_manager.register_context(
            page_file,
            "layout_var",
            layout_func,
            inherit_context=False,
        )

        result = context_manager.collect_context(child_page_file)

        assert "layout_var" not in result.context_data

    def test_collect_inherited_context_dict_return(
        self, context_manager, tmp_path
    ) -> None:
        """Test collecting inherited context with dict return (key=None)."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>",
        )

        page_file = layout_dir / "page.py"
        page_file.write_text("")

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        def layout_dict_func():
            return {"inherited_key1": "value1", "inherited_key2": "value2"}

        context_manager.register_context(
            page_file,
            None,
            layout_dict_func,
            inherit_context=True,
        )

        result = context_manager.collect_context(child_page_file)

        assert "inherited_key1" in result.context_data
        assert "inherited_key2" in result.context_data
        assert result.context_data["inherited_key1"] == "value1"
        assert result.context_data["inherited_key2"] == "value2"


class TestContextMarker:
    """Tests for Context(...) marker used via param.default."""

    def test_context_marker_reads_by_key(self) -> None:
        """Context("key") reads value from context_data by explicit key."""

        def fn(x: str = Context("key")) -> str:
            return x

        r = DependencyResolver()
        resolved = r.resolve_dependencies(fn, _context_data={"key": "value"})
        assert resolved["x"] == "value"

    def test_context_marker_reads_by_param_name(self) -> None:
        """Context() reads context_data by parameter name."""

        def fn(user_id: int = Context()) -> int:
            return user_id

        r = DependencyResolver()
        resolved = r.resolve_dependencies(fn, _context_data={"user_id": 123})
        assert resolved["user_id"] == 123

    def test_context_marker_returns_default_when_missing(self) -> None:
        """Context(..., default=...) returns default when key is missing."""

        def fn(x: str = Context("missing", default="fallback")) -> str:
            return x

        r = DependencyResolver()
        resolved = r.resolve_dependencies(fn, _context_data={})
        assert resolved["x"] == "fallback"

    def test_context_marker_constant_value_mode(self) -> None:
        """Context(value) injects constant value (non-callable, non-str)."""

        def fn(x: int = Context(123)) -> int:
            return x

        r = DependencyResolver()
        resolved = r.resolve_dependencies(fn, _context_data={"x": 999})
        assert resolved["x"] == 123

    def test_context_marker_callable_uses_di(self, mock_http_request) -> None:
        """Context(callable) is called with DI-resolved args."""

        def source(request: HttpRequest) -> str:
            return getattr(request, "path", "")

        def fn(path: str = Context(source)) -> str:
            return path

        r = DependencyResolver()
        request = mock_http_request(path="/from-context/")
        resolved = r.resolve_dependencies(fn, request=request)
        assert resolved["path"] == "/from-context/"

    def test_context_provider_resolve_returns_none_when_default_not_context(
        self,
    ) -> None:
        """Defensive: ContextByDefaultProvider.resolve returns None when default isn't Context."""
        provider = ContextByDefaultProvider(DependencyResolver())
        param = inspect_parameter("x", int, default=123)
        ctx = MagicMock()
        assert provider.resolve(param, ctx) is None


class TestPageContextRegistrySerialize:
    """PageContextRegistry propagates serialize=True through collect_context."""

    @pytest.fixture()
    def registry(self) -> PageContextRegistry:
        """Return a fresh PageContextRegistry for each test."""
        return PageContextRegistry()

    @pytest.mark.parametrize("serialize", [True, False], ids=["serialized", "plain"])
    def test_keyed_serialize_flag_controls_js_context(
        self,
        registry,
        tmp_path,
        serialize,
    ) -> None:
        """A keyed context function with serialize controls js_context inclusion."""
        path = tmp_path / "page.py"
        registry.register_context(path, "my_key", lambda: "val", serialize=serialize)
        result = registry.collect_context(path)
        assert ("my_key" in result.js_context) == serialize

    @pytest.mark.parametrize("serialize", [True, False], ids=["serialized", "plain"])
    def test_dict_merge_serialize_flag_controls_js_context(
        self,
        registry,
        tmp_path,
        serialize,
    ) -> None:
        """An unkeyed context function with serialize controls js_context inclusion."""
        path = tmp_path / "page.py"
        registry.register_context(path, None, lambda: {"k": "v"}, serialize=serialize)
        result = registry.collect_context(path)
        assert ("k" in result.js_context) == serialize

    def test_serialize_keyed_value_stored(self, registry, tmp_path) -> None:
        """The keyed return value is accessible in js_context under the same key."""
        path = tmp_path / "page.py"
        registry.register_context(path, "title", lambda: "Home", serialize=True)
        result = registry.collect_context(path)
        assert result.js_context["title"] == "Home"

    def test_serialize_dict_merge_values_stored(self, registry, tmp_path) -> None:
        """Dict-merge values are individually stored in js_context."""
        path = tmp_path / "page.py"
        registry.register_context(path, None, lambda: {"a": 1, "b": 2}, serialize=True)
        result = registry.collect_context(path)
        assert result.js_context == {"a": 1, "b": 2}

    def test_serialize_first_wins_keyed(self, registry, tmp_path) -> None:
        """First registration of a key in js_context takes priority."""
        path = tmp_path / "page.py"
        registry.register_context(path, "k", lambda: "first", serialize=True)
        registry.register_context(path, "k2", lambda: "second", serialize=True)
        result = registry.collect_context(path)
        assert result.js_context["k"] == "first"

    def test_serialize_dict_merge_wins_over_later_keyed_same_jskey(
        self, registry, tmp_path
    ) -> None:
        """Dict-merge runs before keyed functions, so it wins when both share a js_context key."""
        path = tmp_path / "page.py"
        registry.register_context(
            path, None, lambda: {"shared": "from_dict"}, serialize=True
        )
        registry.register_context(path, "shared", lambda: "from_keyed", serialize=True)
        result = registry.collect_context(path)
        assert result.js_context["shared"] == "from_dict"

    def test_js_context_empty_when_no_serialize(self, registry, tmp_path) -> None:
        """js_context is empty when no context function uses serialize=True."""
        path = tmp_path / "page.py"
        registry.register_context(path, "k", lambda: "v")
        result = registry.collect_context(path)
        assert result.js_context == {}

    def test_js_context_seeded_into_collector_via_render_context(
        self, registry, tmp_path
    ) -> None:
        """collect_context returns js_context that can be fed to a StaticCollector."""
        path = tmp_path / "page.py"
        registry.register_context(path, "page", lambda: "home", serialize=True)
        result = registry.collect_context(path)
        collector = StaticCollector()
        for key, value in result.js_context.items():
            collector.add_js_context(key, value)
        assert collector.js_context()["page"] == "home"

    def test_render_with_serialize_populates_next_init(
        self, registry, tmp_path
    ) -> None:
        """Page.render merges js_context into the collector so Next._init gets it."""
        page_inst = Page()
        path = tmp_path / "page.py"
        page_inst.register_template(path, "{{ title }}<!-- next:scripts -->")
        page_inst._context_manager.register_context(
            path, "title", lambda: "Hello", serialize=True
        )
        html = page_inst.render(path)
        assert '"title":"Hello"' in html
