import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.boundaries.options import BoundaryOptions, DeferMode
from next.components.defer import DeferDeclarations, defer_declarations
from next.components.modules import ModuleLoader
from next.introspect import DeclaringSite
from next.permissions.errors import DeclarationOutsideModuleError


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    defer_declarations.reset()
    yield
    defer_declarations.reset()


def _module(directory: Path, body: str, name: str = "component.py") -> Path:
    """Write a module that imports `component` and runs `body`, then load it."""
    directory.mkdir(parents=True, exist_ok=True)
    module_path = directory / name
    module_path.write_text(f"from next.components import component\n\n{body}\n")
    ModuleLoader().load(module_path)
    return module_path


def _site(path: Path) -> DeclaringSite:
    return DeclaringSite(path, True, {})


class TestComponentDefer:
    def test_a_component_that_declared_nothing_reads_none(self, tmp_path) -> None:
        module_path = _module(tmp_path / "card", "value = 1")
        assert defer_declarations.options(module_path) is None
        assert defer_declarations.options(None) is None

    def test_the_call_binds_its_own_component(self, tmp_path) -> None:
        module_path = _module(
            tmp_path / "card", "component.defer('visible', props=('sku',), timeout=2)"
        )
        assert defer_declarations.options(module_path) == BoundaryOptions(
            mode=DeferMode.VISIBLE, timeout=2.0, props=("sku",)
        )

    def test_the_defaults_stream(self, tmp_path) -> None:
        module_path = _module(tmp_path / "card", "component.defer()")
        assert defer_declarations.options(module_path) == BoundaryOptions(
            mode=DeferMode.STREAM
        )

    def test_props_alone_declare_no_mode(self, tmp_path) -> None:
        module_path = _module(tmp_path / "card", "component.props('sku', 'range')")
        assert defer_declarations.options(module_path) == BoundaryOptions(
            props=("sku", "range")
        )

    @pytest.mark.parametrize(
        ("body", "named"),
        [
            ("component.props()", True),
            ("component.props('sku')", True),
            ("component.defer('stream', props=('sku',))", True),
            ("component.defer('stream')", False),
            ("value = 1", False),
        ],
    )
    def test_naming_props_even_none_makes_a_component_addressable(
        self, tmp_path, body: str, *, named: bool
    ) -> None:
        module_path = _module(tmp_path / "card", body)
        assert defer_declarations.addressable(module_path) is named

    def test_a_module_that_stops_naming_props_is_no_longer_addressable(
        self, tmp_path
    ) -> None:
        module_path = _module(tmp_path / "card", "component.props()")
        version = defer_declarations.version
        module_path.write_text(
            "from next.components import component\n\ncomponent.defer('stream')\n"
        )
        ModuleLoader().load(module_path)
        assert defer_declarations.addressable(module_path) is False
        assert defer_declarations.version > version
        defer_declarations.reset()
        assert defer_declarations.addressable(None) is False

    def test_props_join_a_defer_without_props(self, tmp_path) -> None:
        module_path = _module(
            tmp_path / "card", "component.defer('inline')\ncomponent.props('sku')"
        )
        assert defer_declarations.options(module_path) == BoundaryOptions(
            mode=DeferMode.INLINE, props=("sku",)
        )

    def test_the_last_call_of_a_module_wins(self, tmp_path) -> None:
        module_path = _module(
            tmp_path / "card", "component.defer('load')\ncomponent.defer('manual')"
        )
        options = defer_declarations.options(module_path)
        assert options is not None
        assert options.mode is DeferMode.MANUAL

    def test_props_named_twice_raise_at_the_second_call(self, tmp_path) -> None:
        site = _site(tmp_path / "card" / "component.py")
        defer_declarations.defer(site, {"mode": None, "props": ("a",)})
        expected = (
            f"component.props() in {site.path} declares nothing, since it names "
            "props through both component.defer(props=) and component.props()."
        )
        with pytest.raises(ImproperlyConfigured) as raised:
            defer_declarations.props(site, ("b",))
        assert str(raised.value) == expected
        assert defer_declarations.options(site.path) == BoundaryOptions(props=("a",))

    def test_a_call_inside_a_function_raises(self, tmp_path) -> None:
        site = DeclaringSite(tmp_path / "card" / "component.py", False, {})
        with pytest.raises(DeclarationOutsideModuleError, match="inside a function"):
            defer_declarations.defer(site, {"mode": "load"})
        with pytest.raises(DeclarationOutsideModuleError, match="inside a function"):
            defer_declarations.props(site, ())
        assert defer_declarations.options(site.path) is None

    def test_an_unusable_option_raises_naming_the_file(self, tmp_path) -> None:
        site = _site(tmp_path / "card" / "component.py")
        with pytest.raises(ImproperlyConfigured, match="mode must be one of") as raised:
            defer_declarations.defer(site, {"mode": "lazy"})
        assert str(site.path) in str(raised.value)
        assert defer_declarations.options(site.path) is None

    def test_an_unusable_option_in_a_collected_run_keeps_the_earlier_options(
        self, tmp_path
    ) -> None:
        module_path = _module(tmp_path / "card", "component.defer('load')")
        with (
            defer_declarations.collecting(module_path),
            pytest.raises(ImproperlyConfigured),
        ):
            defer_declarations.defer(_site(module_path), {"mode": "lazy"})
        assert defer_declarations.options(module_path) is None

    def test_a_removed_call_stops_applying_after_a_reload(self, tmp_path) -> None:
        module_path = _module(tmp_path / "card", "component.defer()")
        version = defer_declarations.version
        _module(tmp_path / "card", "value = 1")
        assert defer_declarations.options(module_path) is None
        assert defer_declarations.version == version + 1

    def test_an_unchanged_reload_keeps_the_version(self, tmp_path) -> None:
        _module(tmp_path / "card", "component.defer()")
        version = defer_declarations.version
        _module(tmp_path / "card", "component.defer()")
        assert defer_declarations.version == version

    def test_a_reader_during_the_execution_sees_the_earlier_options(
        self, tmp_path
    ) -> None:
        module_path = _module(tmp_path / "card", "component.defer('load')")
        with defer_declarations.collecting(module_path):
            defer_declarations.defer(_site(module_path), {"mode": "manual"})
            options = defer_declarations.options(module_path)
            assert options is not None
            assert options.mode is DeferMode.LOAD
        options = defer_declarations.options(module_path)
        assert options is not None
        assert options.mode is DeferMode.MANUAL

    def test_a_call_outside_a_collected_run_applies_at_once(self, tmp_path) -> None:
        module_path = tmp_path / "card" / "component.py"
        defer_declarations.props(_site(module_path), ("sku",))
        assert defer_declarations.options(module_path) == BoundaryOptions(
            props=("sku",)
        )

    def test_a_plain_import_keeps_the_declared_mode(self, tmp_path) -> None:
        module_path = _module(
            tmp_path / "card", "component.defer('load')\ncomponent.props('sku')"
        )
        spec = importlib.util.spec_from_file_location("plain_card", module_path)
        assert spec is not None
        assert spec.loader is not None
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        assert defer_declarations.options(module_path) == BoundaryOptions(
            mode=DeferMode.LOAD, props=("sku",)
        )

    def test_calls_outside_a_collected_run_join_each_other(self, tmp_path) -> None:
        module_path = tmp_path / "card" / "component.py"
        defer_declarations.defer(_site(module_path), {"mode": "load"})
        defer_declarations.props(_site(module_path), ("sku",))
        assert defer_declarations.options(module_path) == BoundaryOptions(
            mode=DeferMode.LOAD, props=("sku",)
        )

    def test_a_reload_after_a_plain_import_replaces_the_record(self, tmp_path) -> None:
        module_path = _module(
            tmp_path / "card", "component.defer('load')\ncomponent.props('sku')"
        )
        defer_declarations.props(_site(module_path), ("sku",))
        _module(tmp_path / "card", "component.props('sku')")
        assert defer_declarations.options(module_path) == BoundaryOptions(
            props=("sku",)
        )

    @pytest.mark.parametrize("name", ["page.py", "helpers.py"])
    def test_a_call_outside_a_component_module_declares_nothing(
        self, tmp_path, name: str
    ) -> None:
        source = _module(tmp_path, "component.defer()\ncomponent.props('a')", name=name)
        assert defer_declarations.options(source) is None
        assert defer_declarations.outside() == ((source, "component.props()"),)

    def test_a_reset_drops_every_declaration(self, tmp_path) -> None:
        _module(tmp_path / "a", "component.defer()")
        _module(tmp_path, "component.defer()", name="helpers.py")
        defer_declarations.reset()
        assert defer_declarations.outside() == ()
        assert defer_declarations.options(tmp_path / "a" / "component.py") is None


class TestOnDeferring:
    """A committed delivery mode calls every callback, a bare declaration none."""

    def test_only_a_mode_calls_back(self, tmp_path: Path) -> None:
        store = DeferDeclarations()
        calls: list[str] = []
        store.on_deferring(lambda: calls.append("mode"))
        module_path = tmp_path / "card" / "component.py"
        store.props(_site(module_path), ("sku",))
        assert calls == []
        store.defer(_site(module_path), {"mode": "load"})
        assert calls == ["mode"]


class TestDeferringModules:
    """The modules declaring a delivery mode follow every commit and a reset."""

    def test_a_mode_joins_and_its_withdrawal_leaves(self, tmp_path: Path) -> None:
        store = DeferDeclarations()
        modules = store.deferring_modules()
        module_path = tmp_path / "card" / "component.py"
        store.props(_site(module_path), ("sku",))
        assert modules == set()
        store.defer(_site(module_path), {"mode": "load"})
        assert modules == {module_path}
        store.defer(_site(module_path), {"mode": None})
        assert modules == set()

    def test_a_reset_empties_them(self, tmp_path: Path) -> None:
        store = DeferDeclarations()
        store.defer(_site(tmp_path / "card" / "component.py"), {"mode": "load"})
        store.reset()
        assert store.deferring_modules() == set()
