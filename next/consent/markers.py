"""The consent a visitor gave, per category, as one immutable value."""

from dataclasses import dataclass, field
from typing import Final


NECESSARY: Final = "necessary"
"""The category every visitor grants, since the site does not work without it."""

_FIELDS: Final = frozenset({"allows", "decided", "granted"})


def joint_category(*categories: str) -> str:
    """Return the category a script waits for when every one of `categories` must hold.

    A script declared `marketing` inside a `{% #consented "analytics" %}` block, or a
    block inside another, waits for both. The names join with a space, which no
    category name holds, and `Consent.allows` and the runtime both read it as "all".
    """
    names = sorted(
        {name for category in categories for name in category.split()} - {NECESSARY}
    )
    return " ".join(names) or NECESSARY


@dataclass(frozen=True, slots=True)
class Consent:
    """The categories a visitor granted, `necessary` always among them.

    `decided` stays False until the visitor chooses, every other category denied.
    """

    granted: frozenset[str] = field(default=frozenset({NECESSARY}))
    decided: bool = False

    def allows(self, category: str) -> bool:
        """Whether scripts of `category` may run for this visitor.

        A joint category from `joint_category` is allowed only when each name is.
        """
        return all(
            name == NECESSARY or name in self.granted for name in category.split()
        )

    def __getitem__(self, category: str) -> bool:
        """Answer `{% if consent.marketing %}` like `allows`, leaving fields alone.

        A template tries the key first, so a field name raises to reach the attribute.
        """
        if category in _FIELDS:
            raise KeyError(category)
        return self.allows(category)


UNDECIDED: Final = Consent()
"""The consent of a visitor who has not chosen yet."""


__all__ = ["NECESSARY", "UNDECIDED", "Consent", "joint_category"]
