"""Action-owners implementation bound into the `next.ports` slot at app startup."""

from typing import TYPE_CHECKING, override

from next.ports import ActionOwners

from .checks.sources import action_owners


if TYPE_CHECKING:
    from pathlib import Path


class ActionOwnersImpl(ActionOwners):
    """Binds the port to the actions the form backends registered."""

    @override
    def page_files(self) -> "set[Path]":
        """Return the `page.py` path of each directory whose files declare an action."""
        return action_owners()


__all__ = ["ActionOwnersImpl"]
