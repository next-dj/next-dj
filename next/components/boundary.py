"""What one guarded component render keeps for the error UI it may render instead."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final, cast

from next.defer import DEFERRALS_KEY, ErrorKind
from next.permissions.access import declared
from next.ports import deferrals_slot

from .deferral import defer_declarations


if TYPE_CHECKING:
    from django.template import Context

    from next.ports import DeferralSink

    from .info import ComponentInfo


ADDRESSING: Final = "component"
"""The `addressing` a component boundary reports through `boundary_failed`."""

UNDISCLOSED: Final = frozenset({ErrorKind.FORBIDDEN, ErrorKind.NOT_FOUND})
"""The kinds whose error UI keeps the props hidden and whose wrapper mints no retry."""


def ruled(info: ComponentInfo) -> bool:
    """Whether `info` declares an access rule, which a render may deny."""
    module_path = info.module_path
    return module_path is not None and bool(declared(module_path).requirements)


def deferral_sink(context: Context) -> DeferralSink | None:
    """Return the sink a deferred call of `context` goes to, else the port's."""
    return cast(
        "DeferralSink | None", context.get(DEFERRALS_KEY) or deferrals_slot.peek()
    )


class Attempt:
    """One guarded render of a component, holding what its error UI may disclose."""

    __slots__ = ("granted", "info", "props")

    def __init__(self, info: ComponentInfo) -> None:
        """Start before the props resolved and before the access rule ran."""
        self.info = info
        self.granted = False
        self.props: dict[str, Any] | None = None

    def disclosed(self, kind: ErrorKind) -> dict[str, Any] | None:
        """Return the props the error UI of `kind` reads, `None` when it reads none.

        A refused or a missing subject keeps them hidden, and so does an unpassed rule.
        """
        props = self.props
        if props is None or kind in UNDISCLOSED:
            return None
        if not self.granted and ruled(self.info):
            return None
        options = defer_declarations.options(self.info.module_path)
        names = () if options is None else options.props
        if not names:
            return dict(props)
        return {name: props[name] for name in names if name in props}


current_attempt: ContextVar[Attempt | None] = ContextVar(
    "next_component_attempt", default=None
)
"""The guarded render under way, which a passed access rule of its component marks."""

__all__ = [
    "ADDRESSING",
    "UNDISCLOSED",
    "Attempt",
    "current_attempt",
    "deferral_sink",
    "ruled",
]
