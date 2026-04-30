from __future__ import annotations

from next.pages import context


@context("section")
def section() -> str:
    """Return the identifier used by the layout to mark the active doc section."""
    return "components"
