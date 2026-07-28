import functools
import importlib.util
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from next.components import (
    ComponentContextManager,
    ComponentContextRegistry,
    ComponentInfo,
    ContextFunction,
    DummyBackend,
    FileComponentsBackend,
    _inject_component_context,
    component,
    render_component,
)
from next.components.context import iter_serialized_component_context_keys
from next.static import StaticCollector
from tests.support import attribution, handler_declared_here


class TestComponentContextManager:
    """Tests for ComponentContextManager."""

    def test_component_context_injected_on_render(self, tmp_path: Path) -> None:
        """When component has context registered, _inject_component_context adds it to render."""
        (tmp_path / "comp").mkdir()
        (tmp_path / "comp" / "component.djx").write_text("<span>{{ injected }}</span>")
        (tmp_path / "comp" / "component.py").write_text("# empty\n")
        component._registry.register(
            tmp_path / "comp" / "component.py",
            None,
            lambda: {"injected": "from_context"},
        )
        info = ComponentInfo(
            name="comp",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "comp" / "component.djx",
            module_path=tmp_path / "comp" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {})
        assert "from_context" in html

    def test_component_context_keyed_injected_on_render(self, tmp_path: Path) -> None:
        """Keyed context is injected as context_data[key]."""
        (tmp_path / "k").mkdir()
        (tmp_path / "k" / "component.djx").write_text("<span>{{ count }}</span>")
        (tmp_path / "k" / "component.py").write_text("# empty\n")
        component._registry.register(
            tmp_path / "k" / "component.py", "count", lambda: 42
        )
        info = ComponentInfo(
            name="k",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "k" / "component.djx",
            module_path=tmp_path / "k" / "component.py",
            is_simple=False,
        )
        html = render_component(info, {})
        assert "42" in html

    def test_register_and_get_registry(self) -> None:
        """Context can be registered for a path and retrieved."""
        path = Path("/fake/app/pages/_components/stats/component.py")

        def my_context(request: object) -> dict:
            return {"count": 1}

        component._registry.register(path, "data", my_context)
        funcs = component.get_functions(path)
        assert any(cf.key == "data" for cf in funcs)
        func = next(cf.func for cf in funcs if cf.key == "data")
        assert func(None) == {"count": 1}

    def test_register_reserved_di_key_raises(self) -> None:
        """Cannot use names reserved for resolve_dependencies."""
        path = Path("/fake/app/pages/_components/x/component.py")
        with pytest.raises(ValueError, match="reserved for dependency injection"):
            component._registry.register(path, "request", lambda: None)

    def test_register_duplicate_key_raises(self) -> None:
        """Same context key cannot be registered twice for one component.py."""
        path = Path("/fake/app/pages/_components/y/component.py")

        def f1() -> int:
            return 1

        def f2() -> int:
            return 2

        component._registry.register(path, "slot", f1)
        with pytest.raises(ValueError, match="Duplicate component context"):
            component._registry.register(path, "slot", f2)

    def test_register_same_callable_twice_ok(self) -> None:
        """Re-registering the same function does not raise."""
        path = Path("/fake/app/pages/_components/z/component.py")

        def stable() -> int:
            return 1

        component._registry.register(path, "x", stable)
        component._registry.register(path, "x", stable)


class TestComponentContextRegistryInternals:
    """Duplicate unkeyed, _is_same_function edge cases, __len__."""

    def test_duplicate_unkeyed_raises(self, tmp_path: Path) -> None:
        """Second different unkeyed registration raises with unkeyed message."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "comp" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def first() -> dict[str, int]:
            return {"a": 1}

        def second() -> dict[str, int]:
            return {"b": 2}

        reg.register(p, None, first)
        with pytest.raises(ValueError, match="unkeyed"):
            reg.register(p, None, second)

    def test_len_counts_all_keys(self, tmp_path: Path) -> None:
        """__len__ sums registrations per component path."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "c" / "component.py").resolve()
        p.parent.mkdir(parents=True)
        assert len(reg) == 0

        def fx() -> int:
            return 1

        def fy() -> int:
            return 2

        reg.register(p, "x", fx)
        reg.register(p, "y", fy)
        assert len(reg) == 2

    def test_same_name_from_another_file_is_a_duplicate(self, tmp_path: Path) -> None:
        """Two callables sharing a name but not a file are different functions."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "d" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def local() -> int:
            return 1

        local.__name__ = handler_declared_here.__name__

        reg.register(p, "slot", local)
        with pytest.raises(ValueError, match="Duplicate"):
            reg.register(p, "slot", handler_declared_here)

    def test_unattributable_callable_is_a_duplicate(self, tmp_path: Path) -> None:
        """A callable with no declaring file can never match the registered one."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "e" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def local() -> int:
            return 1

        local.__name__ = "len"

        reg.register(p, "slot", local)
        with pytest.raises(ValueError, match="Duplicate"):
            reg.register(p, "slot", len)

    def test_is_same_function_true_same_file_same_name(self, tmp_path: Path) -> None:
        """An identical name and source file counts as the same function."""

        def h() -> int:
            return 7

        reg = ComponentContextRegistry()
        p = (tmp_path / "f" / "component.py").resolve()
        p.parent.mkdir(parents=True)
        reg.register(p, "x", h)
        reg.register(p, "x", h)

    def test_equivalent_partial_reregisters_without_raising(
        self, tmp_path: Path
    ) -> None:
        """A re-executed module rebuilds its partial, which is not a duplicate."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "g" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def bound(value: int) -> dict[str, int]:
            return {"value": value}

        reg.register(p, "slot", functools.partial(bound, 1))
        reg.register(p, "slot", functools.partial(bound, 1))

        assert len(reg) == 1


class TestComponentContextManagerAttribution:
    """How ComponentContextManager attributes a decorated callable to a file."""

    def test_context_decorator_registers_declaring_test_module(self) -> None:
        """A callable declared here registers under this test module."""
        mgr = ComponentContextManager()

        @mgr.context("here")
        def get_here() -> int:
            return 1

        funcs = mgr.get_functions(Path(__file__))
        assert len(funcs) == 1
        assert funcs[0].func is get_here
        assert mgr._registry.misattributed() == ()

    def test_helper_from_another_module_is_recorded_once(self) -> None:
        """Decorating an imported helper records the pair of files it spans."""
        mgr = ComponentContextManager()

        mgr.context("greeting")(handler_declared_here)
        mgr.context("greeting_again")(handler_declared_here)

        records = mgr._registry.misattributed()
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (
                Path(__file__),
                Path(attribution.__file__).resolve(),
                "handler_declared_here",
            )
        ]

    def test_context_decorator_without_key_registers_caller(
        self, tmp_path: Path
    ) -> None:
        """@mgr.context on a function registers unkeyed context at its own file."""
        script = tmp_path / "comp" / "component.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            textwrap.dedent(
                """
                from next.components import ComponentContextManager
                mgr = ComponentContextManager()

                @mgr.context
                def ctx():
                    return {"v": 1}
                """
            ).lstrip()
        )
        spec = importlib.util.spec_from_file_location("dyn_comp_ctx", script)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mgr = mod.mgr
        funcs = mgr.get_functions(script.resolve())
        assert len(funcs) == 1
        assert funcs[0].key is None

    def test_context_decorator_with_string_key_registers(self, tmp_path: Path) -> None:
        """@mgr.context('key') registers a keyed context for the component module."""
        script = tmp_path / "keyed" / "component.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            textwrap.dedent(
                """
                from next.components import ComponentContextManager
                mgr = ComponentContextManager()

                @mgr.context("slot")
                def get_slot():
                    return 99
                """
            ).lstrip()
        )
        spec = importlib.util.spec_from_file_location("dyn_keyed_ctx", script)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mgr = mod.mgr
        funcs = mgr.get_functions(script.resolve())
        assert len(funcs) == 1
        assert funcs[0].key == "slot"


class TestContextFunctionSerialize:
    """ContextFunction.serialize controls JavaScript context exposure."""

    def test_serialize_true_field_stored(self) -> None:
        """The serialize flag is preserved when set true."""
        fn = ContextFunction(func=dict, key=None, serialize=True)
        assert fn.serialize is True

    def test_serialize_false_field_stored(self) -> None:
        """The serialize flag is preserved when set false."""
        fn = ContextFunction(func=dict, key=None, serialize=False)
        assert fn.serialize is False

    def test_serialize_defaults_to_false(self) -> None:
        """When omitted, serialize defaults to False."""
        fn = ContextFunction(func=dict, key=None)
        assert fn.serialize is False


class TestComponentContextRegistrySerialize:
    """ComponentContextRegistry propagates serialize through register."""

    def test_register_stores_serialize_true(self, tmp_path: Path) -> None:
        """A function registered with serialize=True has the flag set."""
        reg = ComponentContextRegistry()
        path = (tmp_path / "component.py").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        def get_val() -> str:
            return "v"

        reg.register(path, "key", get_val, serialize=True)
        (fn,) = reg.get_functions(path)
        assert fn.serialize is True

    def test_register_default_serialize_false(self, tmp_path: Path) -> None:
        """When serialize is not passed it defaults to False on the stored function."""
        reg = ComponentContextRegistry()
        path = (tmp_path / "component.py").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        def get_val() -> str:
            return "v"

        reg.register(path, "key", get_val)
        (fn,) = reg.get_functions(path)
        assert fn.serialize is False


class TestInjectComponentContextSerialize:
    """_inject_component_context populates StaticCollector when serialize=True."""

    def _setup(
        self, tmp_path: Path
    ) -> tuple[ComponentContextManager, ComponentInfo, Path]:
        """Build a fresh manager, component.py path, and ComponentInfo."""
        mgr = ComponentContextManager()
        module_path = (tmp_path / "component.py").resolve()
        module_path.parent.mkdir(parents=True, exist_ok=True)
        template_path = module_path.parent / "component.djx"
        template_path.write_text("<div></div>")
        info = ComponentInfo(
            name="demo",
            scope_root=module_path.parent,
            scope_relative="",
            template_path=template_path,
            module_path=module_path,
            is_simple=False,
        )
        return mgr, info, module_path

    def test_keyed_serialize_populates_collector(self, tmp_path: Path) -> None:
        """A keyed context function with serialize=True writes to the collector."""
        mgr, info, module_path = self._setup(tmp_path)

        def get_theme() -> str:
            return "dark"

        mgr._registry.register(module_path, "theme", get_theme, serialize=True)

        collector = StaticCollector()
        context_data: dict = {"_static_collector": collector}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)

        assert collector.js_context()["theme"] == "dark"

    def test_dict_merge_serialize_populates_collector(self, tmp_path: Path) -> None:
        """An unkeyed context function with serialize=True writes all keys to collector."""
        mgr, info, module_path = self._setup(tmp_path)

        def get_meta() -> dict:
            return {"env": "prod", "version": "1"}

        mgr._registry.register(module_path, None, get_meta, serialize=True)

        collector = StaticCollector()
        context_data: dict = {"_static_collector": collector}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)

        assert collector.js_context()["env"] == "prod"
        assert collector.js_context()["version"] == "1"

    def test_serialize_false_does_not_populate_collector(self, tmp_path: Path) -> None:
        """A context function without serialize=True does not touch the collector."""
        mgr, info, module_path = self._setup(tmp_path)

        def get_val() -> str:
            return "value"

        mgr._registry.register(module_path, "key", get_val, serialize=False)

        collector = StaticCollector()
        context_data: dict = {"_static_collector": collector}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)

        assert collector.js_context() == {}

    def test_serialize_without_collector_does_not_raise(self, tmp_path: Path) -> None:
        """When no _static_collector is in context_data, serialize is silently skipped."""
        mgr, info, module_path = self._setup(tmp_path)

        def get_val() -> str:
            return "value"

        mgr._registry.register(module_path, "key", get_val, serialize=True)

        context_data: dict = {}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)


class TestComponentContextSerializerOverride:
    """`@component.context(serializer=...)` routes one key through a custom encoder."""

    class _MarkerSerializer:
        """Tracks which values were dumped through the override."""

        def __init__(self) -> None:
            self.calls: list[object] = []

        def dumps(self, value: object) -> str:
            self.calls.append(value)
            return f'"marker:{value}"'

    def _setup(
        self, tmp_path: Path
    ) -> tuple[ComponentContextManager, ComponentInfo, Path]:
        mgr = ComponentContextManager()
        module_path = (tmp_path / "component.py").resolve()
        module_path.parent.mkdir(parents=True, exist_ok=True)
        template_path = module_path.parent / "component.djx"
        template_path.write_text("<div></div>")
        info = ComponentInfo(
            name="demo",
            scope_root=module_path.parent,
            scope_relative="",
            template_path=template_path,
            module_path=module_path,
            is_simple=False,
        )
        return mgr, info, module_path

    def test_keyed_override_recorded_on_collector(self, tmp_path: Path) -> None:
        """A keyed context with `serializer=` records the override on the collector."""
        mgr, info, module_path = self._setup(tmp_path)
        marker = self._MarkerSerializer()
        mgr._registry.register(
            module_path, "feed", lambda: "payload", serialize=True, serializer=marker
        )

        collector = StaticCollector()
        context_data: dict = {"_static_collector": collector}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)

        assert collector.js_context_serializers() == {"feed": marker}
        assert marker.calls == ["payload"]

    def test_dict_merge_override_applies_to_each_key(self, tmp_path: Path) -> None:
        """An unkeyed context with `serializer=` records the override per merged key."""
        mgr, info, module_path = self._setup(tmp_path)
        marker = self._MarkerSerializer()
        mgr._registry.register(
            module_path,
            None,
            lambda: {"a": 1, "b": 2},
            serialize=True,
            serializer=marker,
        )

        collector = StaticCollector()
        context_data: dict = {"_static_collector": collector}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)

        assert collector.js_context_serializers() == {"a": marker, "b": marker}

    def test_no_serializer_keeps_collector_map_empty(self, tmp_path: Path) -> None:
        """Without `serializer=` the override map on the collector stays empty."""
        mgr, info, module_path = self._setup(tmp_path)
        mgr._registry.register(module_path, "k", lambda: "v", serialize=True)

        collector = StaticCollector()
        context_data: dict = {"_static_collector": collector}
        with patch("next.components.renderers.component", mgr):
            _inject_component_context(info, context_data, None)

        assert collector.js_context_serializers() == {}


class TestSerializedComponentContextKeys:
    """iter_serialized_component_context_keys reports what a component.py declares."""

    def _backend(self, tmp_path: Path, body: str) -> FileComponentsBackend:
        """Write a composite component with `body` as its component.py."""
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        (comp_dir / "component.djx").write_text("<div/>")
        (comp_dir / "component.py").write_text(textwrap.dedent(body))
        return FileComponentsBackend(
            {"DIRS": [str(tmp_path)], "COMPONENTS_DIR": "_components"}
        )

    def _keys(self, *backends: object) -> list[tuple[Path, str]]:
        """Enumerate serialized keys with a components manager over `backends`."""
        manager = MagicMock()
        manager._backends = list(backends)
        with patch(
            "next.components.context.get_components_manager", return_value=manager
        ):
            return list(iter_serialized_component_context_keys())

    def test_keyed_serialized_key_is_reported(self, tmp_path: Path) -> None:
        backend = self._backend(
            tmp_path,
            """
            from next.components import context


            @context("$csrf", serialize=True)
            def csrf_token():
                return {"token": "app"}
            """,
        )
        assert self._keys(backend) == [(tmp_path / "widget" / "component.py", "$csrf")]

    def test_unserialized_and_keyless_contexts_are_skipped(
        self, tmp_path: Path
    ) -> None:
        backend = self._backend(
            tmp_path,
            """
            from next.components import context


            @context("plain")
            def plain():
                return 1


            @context(serialize=True)
            def spread():
                return {"$dev": True}
            """,
        )
        assert self._keys(backend) == []

    def test_backend_without_component_files_is_skipped(self, tmp_path: Path) -> None:
        assert self._keys(DummyBackend({})) == []
