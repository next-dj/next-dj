import importlib.util
import inspect
import textwrap
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from next.components import (
    ComponentContextManager,
    ComponentContextRegistry,
    ComponentInfo,
    ContextFunction,
    _inject_component_context,
    component,
    render_component,
)
from next.static import StaticCollector


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
            tmp_path / "k" / "component.py",
            "count",
            lambda: 42,
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

    def test_duplicate_after_getsourcefile_oserror(self, tmp_path: Path) -> None:
        """When inspect.getsourcefile fails, different functions are not 'same'."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "d" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def f1() -> int:
            return 1

        def f2() -> int:
            return 2

        reg.register(p, "slot", f1)
        nope = OSError("nope")
        with (
            patch.object(inspect, "getsourcefile", side_effect=nope),
            pytest.raises(ValueError, match="Duplicate"),
        ):
            reg.register(p, "slot", f2)

    def test_is_same_function_false_when_sourcefile_missing(
        self, tmp_path: Path
    ) -> None:
        """Same __name__ but getsourcefile returns None leads to duplicate error."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "e" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def g1() -> int:
            return 1

        def g2() -> int:
            return 2

        g1.__name__ = "g"
        g2.__name__ = "g"

        reg.register(p, "slot", g1)

        def gs(_: object) -> str | None:
            return None

        with (
            patch.object(inspect, "getsourcefile", gs),
            pytest.raises(ValueError, match="Duplicate"),
        ):
            reg.register(p, "slot", g2)

    def test_is_same_function_true_same_file_same_name(self, tmp_path: Path) -> None:
        """Heuristic: identical name and source file counts as same function."""

        def h() -> int:
            return 7

        reg = ComponentContextRegistry()
        p = (tmp_path / "f" / "component.py").resolve()
        p.parent.mkdir(parents=True)
        reg.register(p, "x", h)
        reg.register(p, "x", h)

    def test_is_same_function_path_compare_raises_typeerror(
        self, tmp_path: Path
    ) -> None:
        """If Path.resolve raises, _is_same_function returns False (except branch)."""
        reg = ComponentContextRegistry()
        p = (tmp_path / "g" / "component.py").resolve()
        p.parent.mkdir(parents=True)

        def u1() -> int:
            return 1

        def u2() -> int:
            return 2

        u1.__name__ = "u"
        u2.__name__ = "u"
        reg.register(p, "slot", u1)

        def gs(fn: object) -> object:
            return str(p) if fn is u1 else 123

        with (
            patch.object(inspect, "getsourcefile", gs),
            pytest.raises(ValueError, match="Duplicate"),
        ):
            reg.register(p, "slot", u2)


class TestComponentContextManagerFrames:
    """How ComponentContextManager finds the caller's file."""

    def test_get_caller_path_raises_when_back_count_too_large(self) -> None:
        """Exceeding frame chain raises RuntimeError."""
        mgr = ComponentContextManager()
        with pytest.raises(RuntimeError, match="Could not determine caller"):
            mgr._get_caller_path(10_000)

    def test_get_caller_path_raises_when_no_python_file_in_chain(self) -> None:
        """Walk stops if no frame exposes a ``.py`` __file__."""
        inner = types.SimpleNamespace(f_back=None, f_globals={"__file__": "/x.txt"})
        start = types.SimpleNamespace(
            f_back=inner,
            f_globals={"__file__": "/y.txt"},
        )
        mgr = ComponentContextManager()
        with (
            patch("next.utils.inspect.currentframe", return_value=start),
            pytest.raises(RuntimeError, match="no __file__ in caller frames"),
        ):
            mgr._get_caller_path(1)

    def test_context_decorator_without_key_registers_caller(
        self, tmp_path: Path
    ) -> None:
        """@mgr.context on a function registers unkeyed context at caller file."""
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
        """@mgr.context('key') uses _get_caller_path(1) and keyed register branch."""
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

    @pytest.mark.parametrize(
        "serialize",
        [True, False],
        ids=["serialize_true", "serialize_false"],
    )
    def test_serialize_field_stored(self, serialize: bool) -> None:  # noqa: FBT001
        """The serialize flag is preserved on the dataclass."""
        fn = ContextFunction(func=dict, key=None, serialize=serialize)
        assert fn.serialize == serialize

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
