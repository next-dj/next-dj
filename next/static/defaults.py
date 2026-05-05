"""Bootstrap registrations for the built-in CSS and JS asset kinds.

The static subsystem is type-agnostic. CSS and JS are not privileged in
core code, they are registered through the same public API that user
projects use to teach the framework about additional file types like
`jsx` or `wasm`.

`register_defaults` is called from the framework `AppConfig.ready` so
the defaults are in place before any request lands. Idempotent
re-registration with identical parameters is allowed and lets test
suites that swap settings during a session keep using the public API.
"""

from __future__ import annotations

from .assets import default_kinds
from .collector import default_placeholders


_STYLES_TOKEN = "<!-- next:styles -->"  # noqa: S105
_SCRIPTS_TOKEN = "<!-- next:scripts -->"  # noqa: S105


def register_defaults() -> None:
    """Register the built-in placeholder slots and the `css` and `js` kinds."""
    default_placeholders.register("styles", token=_STYLES_TOKEN)
    default_placeholders.register("scripts", token=_SCRIPTS_TOKEN)
    default_kinds.register(
        "css",
        extension=".css",
        slot="styles",
        renderer="render_link_tag",
    )
    default_kinds.register(
        "js",
        extension=".js",
        slot="scripts",
        renderer="render_script_tag",
    )
    default_kinds.register(
        "module",
        extension=".mjs",
        slot="scripts",
        renderer="render_module_tag",
    )


__all__ = ["register_defaults"]
