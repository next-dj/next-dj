from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import patch

import pytest

from next.caches import BoundedCache
from next.components import trip
from next.components.context import ContextFunction
from next.components.defer import DeferDeclarations
from next.components.info import ComponentInfo
from next.defer import DeferMode


_resolve = Path.resolve


def _info(
    module: Path | None = None, template: Path | None = None, relative: str = "rel"
) -> ComponentInfo:
    return ComponentInfo(
        name="card",
        scope_root=Path("/nowhere"),
        scope_relative=relative,
        template_path=template,
        module_path=module,
        is_simple=module is None,
    )


def title(sku: Annotated[str, "marker"], n: int, other: float, untyped) -> str:
    return f"{sku}{n}{other}{untyped}"


def second(n: str) -> str:
    return n


@pytest.fixture()
def declared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Declare `card` a load component with props and token overrides."""
    module = tmp_path / "card" / "component.py"
    held = DeferDeclarations()
    held.defer(
        (module, True),
        {"mode": "load", "props": ("sku", "n", "untyped"), "tokens": {"max_age": 9}},
    )
    monkeypatch.setattr(trip, "defer_declarations", held)
    return module


class TestModes:
    @pytest.mark.parametrize(
        "mode", [DeferMode.LOAD, DeferMode.VISIBLE, DeferMode.MANUAL]
    )
    def test_the_browser_modes_are_client_trips(self, mode: DeferMode) -> None:
        assert trip.client_mode(mode)

    @pytest.mark.parametrize("mode", [DeferMode.STREAM, DeferMode.INLINE, None])
    def test_the_other_modes_are_not(self, mode: DeferMode | None) -> None:
        assert not trip.client_mode(mode)


class TestDeclarations:
    def test_props_and_overrides_come_from_the_module(self, declared: Path) -> None:
        info = _info(declared)
        assert trip.declared_props(info) == ("sku", "n", "untyped")
        assert trip.token_overrides(info) == (("max_age", 9),)

    def test_a_component_without_a_declaration_has_none(self) -> None:
        info = _info()
        assert trip.declared_props(info) == ()
        assert trip.token_overrides(info) == ()


class TestScope:
    def test_a_component_without_files_keeps_its_relative_scope(self) -> None:
        assert trip.component_scope(_info()) == "rel"

    def test_an_app_names_the_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(trip, "app_holding", lambda _path: ("shop", "cards"))
        assert trip.component_scope(_info(Path("/x/card/component.py"))) == "shop:cards"

    def test_the_base_directory_anchors_one_outside_every_app(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trip, "app_holding", lambda _path: None)
        monkeypatch.setattr(trip, "resolve_base_dir", lambda: tmp_path)
        template = tmp_path / "ui" / "card" / "component.djx"
        assert trip.component_scope(_info(template=template)) == "ui/card"

    def test_a_directory_outside_the_base_keeps_the_relative_scope(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trip, "app_holding", lambda _path: None)
        monkeypatch.setattr(trip, "resolve_base_dir", lambda: None)
        template = tmp_path / "card" / "component.djx"
        assert trip.component_scope(_info(template=template)) == "rel"


class TestPropTypes:
    def test_a_component_without_a_module_types_nothing(self) -> None:
        assert trip.prop_types(_info()) == {}

    def test_the_first_annotation_of_each_declared_prop_wins(
        self, declared: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        functions = (ContextFunction(title, "title"), ContextFunction(second, "s"))
        registry = SimpleNamespace(
            registry=SimpleNamespace(version=1), get_functions=lambda _path: functions
        )
        monkeypatch.setattr(trip, "component", registry)
        info = _info(declared)
        types = trip.prop_types(info)
        assert types == {"sku": str, "n": int}
        assert trip.prop_types(info) is types
        registry.registry.version = 2
        assert trip.prop_types(info) is not types


class TestAnchoredScope:
    """The scope under `BASE_DIR` is resolved on disk once per directory and base."""

    def test_the_disk_is_read_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trip, "app_holding", lambda _path: None)
        monkeypatch.setattr(trip, "resolve_base_dir", lambda: tmp_path)
        info = _info(template=tmp_path / "once" / "card" / "component.djx")
        with patch.object(Path, "resolve", autospec=True, side_effect=_resolve) as read:
            assert trip.component_scope(info) == "once/card"
            assert trip.component_scope(info) == "once/card"
        assert read.call_count == 2

    def test_another_base_is_resolved_on_its_own(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trip, "app_holding", lambda _path: None)
        info = _info(template=tmp_path / "ui" / "moved" / "component.djx")
        monkeypatch.setattr(trip, "resolve_base_dir", lambda: tmp_path)
        assert trip.component_scope(info) == "ui/moved"
        monkeypatch.setattr(trip, "resolve_base_dir", lambda: tmp_path / "ui")
        assert trip.component_scope(info) == "moved"
        monkeypatch.setattr(trip, "resolve_base_dir", lambda: tmp_path / "other")
        assert trip.component_scope(info) == "rel"

    def test_the_kept_answers_stay_bounded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trip, "_anchored", BoundedCache(2))
        for name in ("a", "b", "c"):
            assert trip._under(tmp_path / name, tmp_path) == name
        assert list(trip._anchored) == [
            (tmp_path / "b", tmp_path),
            (tmp_path / "c", tmp_path),
        ]
