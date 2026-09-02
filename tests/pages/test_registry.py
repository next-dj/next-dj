import functools
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest

import next.pages.registry as registry_module
from next.deps import DependencyResolver
from next.pages import Context, Page
from next.pages.context import ContextByDefaultProvider
from next.pages.registry import PageContextEntry, PageContextRegistry, ZoneBinding
from next.static import StaticCollector
from tests.support import inspect_parameter, record_path_calls


class TestPageContextRegistry:
    """``PageContextRegistry`` storing and collecting ``@context`` functions."""

    def test_init(self, context_manager) -> None:
        """A fresh registry holds no entries."""
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
        self, context_manager, test_file_path, key, func_return, expected_result
    ) -> None:
        """A keyed function fills its key, a keyless one merges its dict."""
        context_manager.register_context(test_file_path, key, func_return)

        assert test_file_path in context_manager._context_registry
        assert key in context_manager._context_registry[test_file_path]
        assert context_manager._context_registry[test_file_path][key] == (
            PageContextEntry(func=func_return, inherit_context=False, serialize=False)
        )

        result = context_manager.collect_context(test_file_path)
        assert result.context_data == expected_result
        assert result.js_context == {}

    def test_collect_context_multiple_functions(
        self, context_manager, test_file_path
    ) -> None:
        """Keyed and keyless functions on one file merge into a single mapping."""

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

        def landing(custom_context_var: str = Context()) -> dict[str, str]:
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
        """A file with no registrations collects an empty context."""
        result = context_manager.collect_context(test_file_path)

        assert result.context_data == {}
        assert result.js_context == {}

    def test_register_context_with_inherit_context(
        self, context_manager, test_file_path
    ) -> None:
        """``inherit_context`` is recorded on the entry at registration time."""

        def test_func() -> str:
            return "inherited_value"

        context_manager.register_context(
            test_file_path, "inherited_key", test_func, inherit_context=True
        )

        assert test_file_path in context_manager._context_registry
        assert "inherited_key" in context_manager._context_registry[test_file_path]
        entry = context_manager._context_registry[test_file_path]["inherited_key"]
        assert entry.func == test_func
        assert entry.inherit_context is True
        assert entry.serialize is False

    def test_collect_inherited_context(self, context_manager, tmp_path) -> None:
        """A child page picks up an inheritable value from the layout directory above it."""
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

        def layout_func() -> str:
            return "layout_value"

        context_manager.register_context(
            page_file, "layout_var", layout_func, inherit_context=True
        )

        result = context_manager.collect_context(child_page_file)

        assert "layout_var" in result.context_data
        assert result.context_data["layout_var"] == "layout_value"

    def test_collect_inherited_context_bounded_depth(
        self, context_manager, tmp_path
    ) -> None:
        """The ancestor walk is bounded by `MAX_ANCESTOR_WALK_DEPTH`.

        This test fabricates a 70-level deep tree, past the 64 cap,
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
        """Inheritable values accumulate down every layout level on the way to the page."""
        root_dir = tmp_path / "root"
        root_dir.mkdir()
        root_layout = root_dir / "layout.djx"
        root_layout.write_text(
            "<html>{% block template %}{% endblock template %}</html>"
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
            root_page, "root_var", root_func, inherit_context=True
        )
        context_manager.register_context(
            sub_page, "sub_var", sub_func, inherit_context=True
        )

        result = context_manager.collect_context(child_page)

        assert "root_var" in result.context_data
        assert "sub_var" in result.context_data
        assert result.context_data["root_var"] == "root_value"
        assert result.context_data["sub_var"] == "sub_value"

    def test_collect_inherited_context_no_layout(
        self, context_manager, tmp_path
    ) -> None:
        """With no layout above it a page inherits nothing."""
        page_file = tmp_path / "page.py"
        result = context_manager.collect_context(page_file)
        assert result.context_data == {}

    def test_collect_inherited_context_no_page_py(
        self, context_manager, tmp_path
    ) -> None:
        """A layout directory without ``page.py`` contributes no inherited context."""
        layout_dir = tmp_path / "layout_dir"
        layout_dir.mkdir()
        layout_file = layout_dir / "layout.djx"
        layout_file.write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )

        child_dir = layout_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        result = context_manager.collect_context(child_page_file)
        assert result.context_data == {}

    def test_collect_inherited_context_without_sibling_layout(
        self, context_manager, tmp_path
    ) -> None:
        """`inherit_context=True` works without a sibling ``layout.djx``.

        The shared HTML envelope can live in a project-level page root
        registered via ``PAGE_BACKENDS["DIRS"]``, in which case
        intermediate ``page.py`` modules do not need a layout sibling
        for their inheritable context to surface on descendant routes.
        """
        section_dir = tmp_path / "section"
        section_dir.mkdir()
        section_page = section_dir / "page.py"
        section_page.write_text("")

        child_dir = section_dir / "child"
        child_dir.mkdir()
        child_page_file = child_dir / "page.py"

        def inheritable() -> str:
            return "section_value"

        context_manager.register_context(
            section_page, "section_var", inheritable, inherit_context=True
        )

        result = context_manager.collect_context(child_page_file)

        assert "section_var" in result.context_data
        assert result.context_data["section_var"] == "section_value"

    def test_collect_inherited_context_inherit_false(
        self, context_manager, tmp_path
    ) -> None:
        """A value registered without ``inherit_context`` stays in its own directory."""
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

        def layout_func() -> str:
            return "layout_value"

        context_manager.register_context(
            page_file, "layout_var", layout_func, inherit_context=False
        )

        result = context_manager.collect_context(child_page_file)

        assert "layout_var" not in result.context_data

    def test_collect_inherited_context_dict_return(
        self, context_manager, tmp_path
    ) -> None:
        """A keyless inheritable function merges its whole dict into the child."""
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

        def layout_dict_func():
            return {"inherited_key1": "value1", "inherited_key2": "value2"}

        context_manager.register_context(
            page_file, None, layout_dict_func, inherit_context=True
        )

        result = context_manager.collect_context(child_page_file)

        assert "inherited_key1" in result.context_data
        assert "inherited_key2" in result.context_data
        assert result.context_data["inherited_key1"] == "value1"
        assert result.context_data["inherited_key2"] == "value2"


class TestZoneBindings:
    """`zone_bindings` gives the checks the zone view of each registration."""

    def test_bindings_carry_key_name_zones_and_callable(
        self, context_manager, test_file_path
    ) -> None:
        def rows() -> list:
            return []

        context_manager.register_context(test_file_path, "rows", rows, zone="table")
        binding = context_manager.zone_bindings()[test_file_path][0]
        assert binding.key == "rows"
        assert binding.name == "rows"
        assert binding.zones == frozenset({"table"})
        assert binding.func is rows

    def test_a_zone_less_registration_binds_to_no_zone(
        self, context_manager, test_file_path
    ) -> None:
        def merged() -> dict:
            return {}

        context_manager.register_context(test_file_path, None, merged)
        assert context_manager.zone_bindings()[test_file_path] == (
            ZoneBinding(key=None, name="merged", zones=None, func=merged),
        )

    def test_an_empty_registry_binds_nothing(self, context_manager) -> None:
        assert context_manager.zone_bindings() == {}


class TestKeylessConflicts:
    """`_keyless_conflicts` records overwritten keyless callables for next.E018."""

    def test_single_keyless_records_no_conflict(
        self, context_manager, test_file_path
    ) -> None:
        def only() -> dict:
            return {}

        context_manager.register_context(test_file_path, None, only)
        assert context_manager._keyless_conflicts == {}

    def test_keyed_alongside_keyless_records_no_conflict(
        self, context_manager, test_file_path
    ) -> None:
        def keyed() -> str:
            return "x"

        def keyless() -> dict:
            return {}

        context_manager.register_context(test_file_path, "k", keyed)
        context_manager.register_context(test_file_path, None, keyless)
        assert context_manager._keyless_conflicts == {}

    def test_multiple_keyless_records_every_name_in_order(
        self, context_manager, test_file_path
    ) -> None:
        def first() -> dict:
            return {}

        def second() -> dict:
            return {}

        def third() -> dict:
            return {}

        for func in (first, second, third):
            context_manager.register_context(test_file_path, None, func)

        assert context_manager._keyless_conflicts[test_file_path] == [
            "first",
            "second",
            "third",
        ]

    def test_reregistering_same_keyless_name_is_no_conflict(
        self, context_manager, test_file_path
    ) -> None:
        def get_context_data() -> dict:
            return {}

        context_manager.register_context(test_file_path, None, get_context_data)
        context_manager.register_context(test_file_path, None, get_context_data)

        assert context_manager._keyless_conflicts == {}

    def test_partial_and_function_conflict_records_unwrapped_names(
        self, context_manager, test_file_path
    ) -> None:
        def bound() -> dict:
            return {}

        def plain() -> dict:
            return {}

        context_manager.register_context(test_file_path, None, functools.partial(bound))
        context_manager.register_context(test_file_path, None, plain)

        assert context_manager._keyless_conflicts[test_file_path] == ["bound", "plain"]

    def test_reset_clears_keyless_conflicts(
        self, context_manager, test_file_path
    ) -> None:
        def first() -> dict:
            return {}

        def second() -> dict:
            return {}

        context_manager.register_context(test_file_path, None, first)
        context_manager.register_context(test_file_path, None, second)
        assert context_manager._keyless_conflicts

        context_manager.reset()
        assert context_manager._keyless_conflicts == {}
        assert context_manager._context_registry == {}


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
        """``ContextByDefaultProvider.resolve`` yields ``None`` for a non-``Context`` default."""
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
        self, registry, tmp_path, serialize
    ) -> None:
        """A keyed context function with serialize controls js_context inclusion."""
        path = tmp_path / "page.py"
        registry.register_context(path, "my_key", lambda: "val", serialize=serialize)
        result = registry.collect_context(path)
        assert ("my_key" in result.js_context) == serialize

    @pytest.mark.parametrize("serialize", [True, False], ids=["serialized", "plain"])
    def test_dict_merge_serialize_flag_controls_js_context(
        self, registry, tmp_path, serialize
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


class TestPageContextRegistrySerializerOverride:
    """`@page.context(serializer=...)` routes one key through a custom serializer."""

    class _MarkerSerializer:
        """Tracks which values were dumped through the override."""

        def __init__(self) -> None:
            self.calls: list[object] = []

        def dumps(self, value: object) -> str:
            self.calls.append(value)
            return f'"marker:{value}"'

    @pytest.fixture()
    def registry(self) -> PageContextRegistry:
        """Return a fresh PageContextRegistry for each test."""
        return PageContextRegistry()

    def test_keyed_override_recorded(self, registry, tmp_path) -> None:
        """A keyed context with `serializer=` records the override under the key."""
        path = tmp_path / "page.py"
        marker = self._MarkerSerializer()
        registry.register_context(
            path, "feed", lambda: {"x": 1}, serialize=True, serializer=marker
        )
        result = registry.collect_context(path)
        assert result.js_context_serializers == {"feed": marker}

    def test_dict_merge_override_applies_to_each_key(self, registry, tmp_path) -> None:
        """An unkeyed context with `serializer=` records the override per merged key."""
        path = tmp_path / "page.py"
        marker = self._MarkerSerializer()
        registry.register_context(
            path, None, lambda: {"a": 1, "b": 2}, serialize=True, serializer=marker
        )
        result = registry.collect_context(path)
        assert result.js_context_serializers == {"a": marker, "b": marker}

    def test_no_serializer_keeps_map_empty(self, registry, tmp_path) -> None:
        """Without `serializer=` the override map stays empty."""
        path = tmp_path / "page.py"
        registry.register_context(path, "k", lambda: "v", serialize=True)
        result = registry.collect_context(path)
        assert result.js_context_serializers == {}

    def test_render_routes_override_key_through_marker(self, tmp_path) -> None:
        """A render carries the override serializer's key through to the init script."""
        marker = self._MarkerSerializer()
        page_inst = Page()
        path = tmp_path / "page.py"
        page_inst.register_template(path, "{{ k }}<!-- next:scripts -->")
        page_inst._context_manager.register_context(
            path, "k", lambda: "v", serialize=True, serializer=marker
        )
        page_inst._context_manager.register_context(
            path, "other", lambda: "plain", serialize=True
        )
        html = page_inst.render(path)
        assert '"k":"marker:v"' in html
        assert '"other":"plain"' in html
        assert marker.calls.count("v") >= 1
        assert all(call == "v" for call in marker.calls)


def _count_chain_builds(monkeypatch) -> list[Path]:
    """Collect every ancestor-chain build the registry performs."""
    builds: list[Path] = []
    original = registry_module.page_path_info

    def counting(file_path: Path):
        builds.append(file_path)
        return original(file_path)

    monkeypatch.setattr(registry_module, "page_path_info", counting)
    return builds


def _build_ancestor_page(tmp_path: Path) -> tuple[Path, Path]:
    """Write an ancestor ``page.py`` above a child page and return both."""
    ancestor = tmp_path / "page.py"
    ancestor.write_text("x = 1")
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    child = child_dir / "page.py"
    child.write_text("x = 1")
    return ancestor, child


class TestContextRegistryVersion:
    """The version counter every per-path memo keys off."""

    def test_a_fresh_registry_starts_at_zero(self, context_manager) -> None:
        """Nothing registered means nothing to invalidate."""
        assert context_manager.version == 0

    def test_every_registration_bumps_the_version(
        self, context_manager, test_file_path
    ) -> None:
        """Each write moves the counter, including a rewrite of the same key."""
        context_manager.register_context(test_file_path, "k", lambda: "v")
        assert context_manager.version == 1

        context_manager.register_context(test_file_path, "k", lambda: "w")
        assert context_manager.version == 2

    def test_reset_bumps_the_version(self, context_manager, test_file_path) -> None:
        """Clearing the registry invalidates the memos with it."""
        context_manager.register_context(test_file_path, "k", lambda: "v")
        before = context_manager.version

        context_manager.reset()

        assert context_manager.version > before

    def test_reset_drops_the_inherited_chain(self, context_manager, tmp_path) -> None:
        """A collected chain does not survive the registry it came from."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "shared", lambda: "v", inherit_context=True
        )
        assert context_manager.collect_context(child).context_data == {"shared": "v"}

        context_manager.reset()

        assert context_manager.collect_context(child).context_data == {}

    def test_a_new_ancestor_provider_reaches_the_next_collect(
        self, context_manager, tmp_path
    ) -> None:
        """Registering upstream invalidates the memoised chain of the child."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "first", lambda: "1", inherit_context=True
        )
        assert context_manager.collect_context(child).context_data == {"first": "1"}

        context_manager.register_context(
            ancestor, "second", lambda: "2", inherit_context=True
        )

        assert context_manager.collect_context(child).context_data == {
            "first": "1",
            "second": "2",
        }


class TestInheritedChainMemo:
    """The ancestor chain is read out of the registry, not off the disk."""

    def test_a_second_collect_reuses_the_memoised_chain(
        self, context_manager, tmp_path, monkeypatch
    ) -> None:
        """The chain is assembled once for a run of collects."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "shared", lambda: "v", inherit_context=True
        )
        builds = _count_chain_builds(monkeypatch)

        context_manager.collect_context(child)
        context_manager.collect_context(child)

        assert builds == [child]

    def test_a_warm_collect_probes_no_directory(
        self, context_manager, tmp_path, monkeypatch
    ) -> None:
        """A repeat collect performs no ``exists`` call of its own."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "shared", lambda: "v", inherit_context=True
        )
        context_manager.collect_context(child)
        probes = record_path_calls(monkeypatch, "exists")

        context_manager.collect_context(child)

        assert probes == []

    def test_a_page_py_deleted_from_disk_keeps_its_live_registration(
        self, context_manager, tmp_path
    ) -> None:
        """A registration outliving its file still reaches descendants."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "shared", lambda: "v", inherit_context=True
        )

        ancestor.unlink()

        assert context_manager.collect_context(child).context_data == {"shared": "v"}


class TestMergeOrder:
    """The order the merge consumes this file's callables."""

    def test_keyless_runs_first_then_keyed_in_string_order(
        self, context_manager, test_file_path
    ) -> None:
        """Registration order does not decide the merge, the key string does."""
        calls: list[str] = []

        def record(name: str, value):
            def run():
                calls.append(name)
                return value

            return run

        context_manager.register_context(test_file_path, "b", record("b", "vb"))
        context_manager.register_context(test_file_path, "10", record("10", "v10"))
        context_manager.register_context(
            test_file_path, None, record("keyless", {"merged": "m"})
        )
        context_manager.register_context(test_file_path, "a", record("a", "va"))
        context_manager.register_context(test_file_path, "2", record("2", "v2"))

        result = context_manager.collect_context(test_file_path)

        assert calls == ["keyless", "10", "2", "a", "b"]
        assert result.context_data == {
            "merged": "m",
            "10": "v10",
            "2": "v2",
            "a": "va",
            "b": "vb",
        }

    def test_a_keyed_value_overwrites_the_dict_merge_that_shares_its_name(
        self, context_manager, test_file_path
    ) -> None:
        """Keyless first means a keyed callable owns the final value."""
        context_manager.register_context(
            test_file_path, None, lambda: {"shared": "from_merge"}
        )
        context_manager.register_context(test_file_path, "shared", lambda: "from_key")

        result = context_manager.collect_context(test_file_path)

        assert result.context_data["shared"] == "from_key"

    def test_the_order_survives_a_memo_rebuild(
        self, context_manager, test_file_path
    ) -> None:
        """A registration in between does not reshuffle the callables around it."""
        calls: list[str] = []

        def record(name: str):
            def run() -> str:
                calls.append(name)
                return name

            return run

        context_manager.register_context(test_file_path, "b", record("b"))
        context_manager.register_context(test_file_path, "a", record("a"))
        context_manager.collect_context(test_file_path)

        context_manager.register_context(test_file_path, "c", record("c"))
        calls.clear()
        context_manager.collect_context(test_file_path)

        assert calls == ["a", "b", "c"]


class TestZoneBatchWithInheritedChain:
    """A zone batch narrows this file's callables and leaves the chain alone."""

    def test_inherited_runs_while_a_foreign_zone_callable_is_skipped(
        self, context_manager, tmp_path
    ) -> None:
        """The chain has no zone binding, so a batch never gates it."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "shared", lambda: "inherited", inherit_context=True
        )
        context_manager.register_context(child, "always", lambda: "own")
        context_manager.register_context(child, "table", lambda: "rows", zone="table")

        result = context_manager.collect_context(
            child, _requested_zones=frozenset({"other"})
        )

        assert result.context_data == {"shared": "inherited", "always": "own"}

    def test_the_memoised_chain_serves_every_batch(
        self, context_manager, tmp_path, monkeypatch
    ) -> None:
        """Batches differ per request, the chain behind them does not."""
        ancestor, child = _build_ancestor_page(tmp_path)
        context_manager.register_context(
            ancestor, "shared", lambda: "inherited", inherit_context=True
        )
        context_manager.register_context(child, "table", lambda: "rows", zone="table")
        builds = _count_chain_builds(monkeypatch)

        first = context_manager.collect_context(
            child, _requested_zones=frozenset({"table"})
        )
        second = context_manager.collect_context(
            child, _requested_zones=frozenset({"other"})
        )

        assert builds == [child]
        assert first.context_data == {"shared": "inherited", "table": "rows"}
        assert second.context_data == {"shared": "inherited"}
