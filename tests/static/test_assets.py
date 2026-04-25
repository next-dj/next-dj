from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.static import KindRegistry, StaticAsset, default_kinds
from next.static.assets import StaticNamespace


if TYPE_CHECKING:
    from pathlib import Path


CSS_URL = "https://example.com/a.css"


class TestStaticAsset:
    """StaticAsset is a slotted, frozen value object."""

    def test_defaults(self) -> None:
        asset = StaticAsset(url=CSS_URL, kind="css")
        assert asset.url == CSS_URL
        assert asset.kind == "css"
        assert asset.source_path is None
        assert asset.inline is None

    def test_with_source_path(self, tmp_path: Path) -> None:
        asset = StaticAsset(url=CSS_URL, kind="css", source_path=tmp_path)
        assert asset.source_path == tmp_path

    def test_is_frozen(self) -> None:
        asset = StaticAsset(url=CSS_URL, kind="css")
        with pytest.raises(Exception):  # noqa: B017, PT011
            asset.url = "mutated"  # type: ignore[misc]


class TestKindRegistry:
    """KindRegistry maps kinds to file extensions with mutation support."""

    def test_default_kinds(self) -> None:
        reg = KindRegistry()
        assert reg.extension("css") == ".css"
        assert reg.extension("js") == ".js"

    def test_contains(self) -> None:
        reg = KindRegistry()
        assert "css" in reg
        assert "unknown" not in reg
        assert 42 not in reg  # type: ignore[comparison-overlap]

    def test_register_new_kind(self) -> None:
        reg = KindRegistry()
        reg.register("wasm", ".wasm")
        assert reg.extension("wasm") == ".wasm"
        assert "wasm" in reg

    def test_register_overrides_existing(self) -> None:
        reg = KindRegistry()
        reg.register("css", ".scss")
        assert reg.extension("css") == ".scss"

    def test_extension_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.extension("ghost")

    def test_register_rejects_empty_kind(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Invalid kind"):
            reg.register("", ".x")

    def test_register_rejects_non_identifier_kind(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Invalid kind"):
            reg.register("has-dash", ".x")

    def test_register_rejects_extension_without_dot(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="must start with"):
            reg.register("foo", "foo")

    def test_kind_for_extension(self) -> None:
        reg = KindRegistry()
        assert reg.kind_for_extension(".css") == "css"
        assert reg.kind_for_extension(".missing") is None

    def test_kinds_preserves_order(self) -> None:
        reg = KindRegistry()
        reg.register("wasm", ".wasm")
        assert reg.kinds() == ("css", "js", "wasm")


class TestDefaultKinds:
    """The module-level default_kinds is shared across the framework."""

    def test_is_a_kind_registry(self) -> None:
        assert isinstance(default_kinds, KindRegistry)

    def test_ships_css_and_js(self) -> None:
        assert "css" in default_kinds
        assert "js" in default_kinds


class TestStaticNamespace:
    """StaticNamespace exposes string constants for URL construction."""

    def test_next_namespace(self) -> None:
        assert StaticNamespace.NEXT == "next"
