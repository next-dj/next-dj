"""The one decision whether a request may render a component, asked by every path.

An inline render, a deferred shell, a client trip, a reload and an error UI read it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from next.permissions.access import check, declared


if TYPE_CHECKING:
    from next.permissions.access import Lookup
    from next.permissions.markers import Denial

    from .info import ComponentInfo


class AccessVerdict(enum.Enum):
    """What a decision says of one component call."""

    OPEN = "open"
    """The component declares no access rule."""

    GRANTED = "granted"
    """The rule passed, its objects read through the lookup the caller gave."""

    PENDING = "pending"
    """Every requirement without an object passed, the rest waits for its objects."""

    DENIED = "denied"
    """The rule refused, `denial` saying why."""


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """The verdict of one component call, with the denial of a refused one."""

    verdict: AccessVerdict
    denial: Denial | None = None

    @property
    def passed(self) -> bool:
        """Whether the call may show what the component renders."""
        return (
            self.verdict is AccessVerdict.OPEN or self.verdict is AccessVerdict.GRANTED
        )


OPEN: Final = AccessDecision(AccessVerdict.OPEN)
GRANTED: Final = AccessDecision(AccessVerdict.GRANTED)
PENDING: Final = AccessDecision(AccessVerdict.PENDING)


def decide(
    info: ComponentInfo, request: object, lookup: Lookup | None = None
) -> AccessDecision:
    """Return whether `request` may render `info`, its objects read through `lookup`.

    Without a lookup an object requirement leaves the decision pending, never denied.
    """
    module_path = info.module_path
    if module_path is None:
        return OPEN
    rule = declared(module_path)
    if not rule.requirements:
        return OPEN
    denial = check(getattr(request, "user", None), rule, lookup)
    if denial is None:
        return GRANTED
    waits = denial.reason == "permission" and denial.requirement.obj is not None
    if lookup is None and waits:
        return PENDING
    return AccessDecision(AccessVerdict.DENIED, denial)


def ruled(info: ComponentInfo) -> bool:
    """Whether `info` declares an access rule, which a render may deny."""
    module_path = info.module_path
    return module_path is not None and bool(declared(module_path).requirements)


def passed(info: ComponentInfo, *, granted: bool) -> bool:
    """Whether the error or loading UI of `info` may show what a passed rule shows."""
    return granted or not ruled(info)


__all__ = [
    "GRANTED",
    "OPEN",
    "PENDING",
    "AccessDecision",
    "AccessVerdict",
    "decide",
    "passed",
    "ruled",
]
