"""The consent a visitor gave, per category, as one immutable value."""

from dataclasses import dataclass, field
from typing import Final


NECESSARY: Final = "necessary"
"""The category every visitor grants, since the site does not work without it."""

_FIELDS: Final = frozenset({"allows", "decided", "granted"})


@dataclass(frozen=True, slots=True)
class Consent:
    """The categories a visitor granted, `necessary` always among them.

    `decided` stays False until the visitor chooses, every other category denied.
    """

    granted: frozenset[str] = field(default=frozenset({NECESSARY}))
    decided: bool = False

    def allows(self, category: str) -> bool:
        """Whether scripts of `category` may run for this visitor."""
        return category == NECESSARY or category in self.granted

    def __getitem__(self, category: str) -> bool:
        """Answer `{% if consent.marketing %}` like `allows`, leaving fields alone.

        A template tries the key first, so a field name raises to reach the attribute.
        """
        if category in _FIELDS:
            raise KeyError(category)
        return self.allows(category)


UNDECIDED: Final = Consent()
"""The consent of a visitor who has not chosen yet."""


__all__ = ["NECESSARY", "UNDECIDED", "Consent"]
