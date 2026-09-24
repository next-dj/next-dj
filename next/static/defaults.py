"""Bootstrap registrations for the built-in `css`, `js`, and `module` asset kinds.

The built-ins register through the same public API a project uses for `jsx` or `wasm`.
"""

from __future__ import annotations

from .assets import default_kinds
from .collector import default_placeholders


_STYLES_MARKER = "<!-- next:styles -->"
_SCRIPTS_MARKER = "<!-- next:scripts -->"


def register_defaults() -> None:
    """Register the built-in placeholder slots and asset kinds."""
    default_placeholders.register("styles", token=_STYLES_MARKER)
    default_placeholders.register("scripts", token=_SCRIPTS_MARKER)
    default_kinds.register(
        "css",
        extension=".css",
        slot="styles",
        renderer="render_link_tag",
        inline_tag="style",
    )
    default_kinds.register(
        "js",
        extension=".js",
        slot="scripts",
        renderer="render_script_tag",
        inline_tag="script",
    )
    default_kinds.register(
        "module", extension=".mjs", slot="scripts", renderer="render_module_tag"
    )


__all__ = ["register_defaults"]
