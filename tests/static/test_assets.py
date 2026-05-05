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


class TestKindRegistryStartsEmpty:
    """A fresh registry ships with zero registered kinds."""

    def test_empty_after_init(self) -> None:
        reg = KindRegistry()
        assert reg.kinds() == ()
        assert "css" not in reg


class TestKindRegistryRegister:
    """register stores extension, slot, and renderer per kind."""

    def test_register_makes_lookups_succeed(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.extension("css") == ".css"
        assert reg.slot("css") == "styles"
        assert reg.renderer("css") == "render_link_tag"
        assert "css" in reg

    def test_register_is_idempotent_for_identical_params(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.kinds() == ("css",)

    def test_register_rejects_conflicting_re_registration(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        with pytest.raises(ValueError, match="already registered"):
            reg.register(
                "css",
                extension=".sass",
                slot="styles",
                renderer="render_link_tag",
            )

    def test_register_rejects_empty_kind(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Invalid kind"):
            reg.register("", extension=".x", slot="s", renderer="r")

    def test_register_rejects_non_identifier_kind(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Invalid kind"):
            reg.register("has-dash", extension=".x", slot="s", renderer="r")

    def test_register_rejects_extension_without_dot(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="must start with"):
            reg.register("foo", extension="foo", slot="s", renderer="r")

    def test_register_rejects_empty_slot(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Slot name"):
            reg.register("foo", extension=".x", slot="", renderer="r")

    def test_register_rejects_empty_renderer(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Renderer"):
            reg.register("foo", extension=".x", slot="s", renderer="")


class TestKindRegistryLookups:
    """Lookups raise KeyError on unregistered kinds."""

    def test_extension_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.extension("ghost")

    def test_slot_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.slot("ghost")

    def test_renderer_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.renderer("ghost")

    def test_kind_for_extension(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.kind_for_extension(".css") == "css"
        assert reg.kind_for_extension(".missing") is None

    def test_kinds_preserves_order(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        reg.register(
            "js", extension=".js", slot="scripts", renderer="render_script_tag"
        )
        reg.register("jsx", extension=".jsx", slot="scripts", renderer="render_babel")
        assert reg.kinds() == ("css", "js", "jsx")

    def test_contains_rejects_non_string(self) -> None:
        reg = KindRegistry()
        assert 42 not in reg  # type: ignore[comparison-overlap]


class TestDefaultKinds:
    """The module-level default_kinds is shared across the framework."""

    def test_is_a_kind_registry(self) -> None:
        assert isinstance(default_kinds, KindRegistry)

    def test_bootstrap_registered_css_and_js(self) -> None:
        assert "css" in default_kinds
        assert "js" in default_kinds
        assert default_kinds.slot("css") == "styles"
        assert default_kinds.slot("js") == "scripts"
        assert default_kinds.renderer("css") == "render_link_tag"
        assert default_kinds.renderer("js") == "render_script_tag"


class TestStaticNamespace:
    """StaticNamespace exposes string constants for URL construction."""

    def test_next_namespace(self) -> None:
        assert StaticNamespace.NEXT == "next"
