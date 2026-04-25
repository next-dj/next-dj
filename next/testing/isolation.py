"""Helpers to drop registry state between tests.

Most Django tests expect module-level registrations to persist. These
helpers are opt-in and intended for tests that explicitly verify
registry behaviour or need to reload backends after swapping settings.
"""

from __future__ import annotations

from next.components.manager import components_manager
from next.forms.manager import form_action_manager
from next.pages.manager import page


def reset_form_actions() -> None:
    """Clear every form action registry reachable from `form_action_manager`."""
    form_action_manager.clear_registries()


def reset_components() -> None:
    """Drop cached component backends so the next render reloads them."""
    components_manager._reload_config()


def reset_registries() -> None:
    """Reset form and component registries in one call.

    Opt-in helper. Invoke when a test deliberately changes
    `NEXT_FRAMEWORK` settings or registers conflicting fixtures.
    """
    reset_form_actions()
    reset_components()


def reset_page_cache() -> None:
    """Drop the page template cache and source-mtime bookkeeping.

    Needed between iterations of `render_page` against rewritten files
    on disk (for example in `tmp_path`), because `page.render` memoises
    composed template strings per file path.
    """
    page._template_registry.clear()
    page._template_source_mtimes.clear()


__all__ = [
    "reset_components",
    "reset_form_actions",
    "reset_page_cache",
    "reset_registries",
]
