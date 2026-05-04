from __future__ import annotations

from next.pages import context


@context("settings_active")
def settings_active() -> bool:
    """Mark the settings tab as active for the layout toolbar."""
    return True
