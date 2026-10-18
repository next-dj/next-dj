"""System checks for the boundaries between the project applications.

They live in a registry of their own, which `check --tag boundaries` alone runs.
"""

from collections.abc import Callable, Mapping
from typing import Final

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning
from django.core.checks.registry import CheckRegistry

from next.checks import BOUNDARIES
from next.conf.checks import check_next_framework_boundaries_mode

from .declared import read_declarations
from .manager import boundaries_manager
from .rules import RULES, Finding


_LEVELS: Final[Mapping[str, Callable[..., CheckMessage]]] = {
    "error": Error,
    "warning": DjangoWarning,
}

boundary_checks: Final = CheckRegistry()


def _message(finding: Finding, mode: str) -> CheckMessage:
    """Return the check message of one finding, its hint pointing to the doctrine."""
    rule = RULES[finding.code]
    return _LEVELS[rule.level_in(mode)](
        finding.message, hint=rule.full_hint, obj=finding.where or None, id=finding.code
    )


@boundary_checks.register(BOUNDARIES)
def check_boundaries(*args, **kwargs) -> list[CheckMessage]:
    """Report every boundary finding no record of `boundaries.toml` covers.

    A cycle is a warning under `warn` and an error under `strict`, the same id in both.
    """
    if not boundaries_manager.enabled:
        return []
    mode = boundaries_manager.mode
    off = boundaries_manager.disabled()
    if off is not None:
        return [_message(off, mode)]
    findings = boundaries_manager.report().findings
    return [_message(finding, mode) for finding in findings]


@boundary_checks.register(BOUNDARIES)
def check_declarations(*args, **kwargs) -> list[CheckMessage]:
    """Report a broken boundary attribute of an `AppConfig` and an app it misses.

    It needs no import graph, so each deploy checks its own settings cheaply.
    """
    if not boundaries_manager.enabled:
        return []
    mode = boundaries_manager.mode
    return [_message(finding, mode) for finding in read_declarations().findings]


@boundary_checks.register(BOUNDARIES)
def check_boundaries_mode(*args, **kwargs) -> list[CheckMessage]:
    """Report a `BOUNDARIES` value that names no mode, as the `next` tag does.

    `manage.py check` leaves it out of a run that has the `next` tag, so it shows once.
    """
    return check_next_framework_boundaries_mode(*args, **kwargs)


__all__ = [
    "boundary_checks",
    "check_boundaries",
    "check_boundaries_mode",
    "check_declarations",
]
