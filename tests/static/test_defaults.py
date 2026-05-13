from __future__ import annotations

from next.static import default_kinds, default_placeholders
from next.static.defaults import register_defaults


class TestRegisterDefaults:
    """`register_defaults` populates kinds and slots through the public API."""

    def test_registers_styles_and_scripts_slots(self) -> None:
        register_defaults()
        styles = default_placeholders.get("styles")
        scripts = default_placeholders.get("scripts")
        assert styles is not None
        assert scripts is not None
        assert styles.token == "<!-- next:styles -->"
        assert scripts.token == "<!-- next:scripts -->"

    def test_registers_css_and_js_kinds(self) -> None:
        register_defaults()
        assert default_kinds.extension("css") == ".css"
        assert default_kinds.extension("js") == ".js"
        assert default_kinds.slot("css") == "styles"
        assert default_kinds.slot("js") == "scripts"
        assert default_kinds.renderer("css") == "render_link_tag"
        assert default_kinds.renderer("js") == "render_script_tag"

    def test_registers_module_kind(self) -> None:
        register_defaults()
        assert default_kinds.extension("module") == ".mjs"
        assert default_kinds.slot("module") == "scripts"
        assert default_kinds.renderer("module") == "render_module_tag"

    def test_is_idempotent(self) -> None:
        register_defaults()
        register_defaults()
        assert "css" in default_kinds
        assert "js" in default_kinds
        assert "module" in default_kinds
