"""What a guarded component render keeps for its error UI, and where deferrals go."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final, cast

from next.boundaries.calls import DEFERRALS_KEY
from next.boundaries.failures import ErrorKind
from next.ports import deferrals_slot

from .access import passed
from .defer import defer_declarations


if TYPE_CHECKING:
    from django.template import Context

    from next.ports import DeferralSink

    from .info import ComponentInfo


UNDISCLOSED: Final = frozenset({ErrorKind.FORBIDDEN, ErrorKind.NOT_FOUND})
"""The kinds whose error UI keeps the props hidden and whose wrapper mints no retry."""


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

    @property
    def passed(self) -> bool:
        """Whether the access rule of the component passed, or it declares none."""
        return passed(self.info, granted=self.granted)

    def disclosed(self, kind: ErrorKind) -> dict[str, Any] | None:
        """Return the props the error UI of `kind` reads, `None` when it reads none.

        A refused or a missing subject keeps them hidden, and so does an unpassed rule.
        """
        props = self.props
        if props is None or kind in UNDISCLOSED or not self.passed:
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

__all__ = ["UNDISCLOSED", "Attempt", "current_attempt", "deferral_sink"]
