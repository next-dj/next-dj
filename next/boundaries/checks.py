"""System checks for the boundaries between the project applications.

They run under the `boundaries` tag alone, so `check --tag next` leaves them out.
"""

from collections.abc import Callable, Mapping
from typing import Final

from django.core.checks import (
    CheckMessage,
    Error,
    Info,
    Warning as DjangoWarning,
    register,
)

from next.checks import BOUNDARIES

from .manager import boundaries_manager
from .rules import RULES, Finding


_LEVELS: Final[Mapping[str, Callable[..., CheckMessage]]] = {
    "error": Error,
    "warning": DjangoWarning,
    "info": Info,
}


def _message(finding: Finding) -> CheckMessage:
    """Return the check message of one finding, its hint pointing to the doctrine."""
    rule = RULES[finding.code]
    where = f"{finding.file}:{finding.line}" if finding.line else finding.file
    return _LEVELS[rule.level](
        finding.message, hint=rule.full_hint, obj=where or None, id=finding.code
    )


@register(BOUNDARIES)
def check_boundaries(*args, **kwargs) -> list[CheckMessage]:
    """Report every boundary finding no record of `boundaries.toml` covers.

    Unchecked boundaries give one message, `next.W148` in the strict mode.
    """
    off = boundaries_manager.disabled()
    if off is not None:
        return [_message(off)]
    return [_message(finding) for finding in boundaries_manager.report().findings]


__all__ = ["check_boundaries"]
