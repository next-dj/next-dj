"""The sentinel the configuration layer and the areas above it mark an absent value by.

It lives here because this layer imports nothing else, so no import cycle can form.
"""

from __future__ import annotations

import enum
from typing import Final


class Unset(enum.Enum):
    """The type of `UNSET`, the framework sentinel for an absent value."""

    UNSET = enum.auto()


UNSET: Final = Unset.UNSET
"""The marker of an absent value, such as an unread memo, distinct from `None`."""


__all__ = ["UNSET", "Unset"]
