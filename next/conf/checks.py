"""System checks for the configuration layer."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import CheckMessage, register

from next.checks import NEXT, common

from .settings import NextFrameworkSettings


@register(NEXT)
def check_next_framework_unknown_top_level_keys(*args, **kwargs) -> list[CheckMessage]:
    """Reject keys under `NEXT_FRAMEWORK` that are not defined in defaults."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is None or not isinstance(raw, dict):
        return []
    allowed = frozenset(NextFrameworkSettings.DEFAULTS.keys())
    # The module is imported rather than the name, because `next.checks.common`
    # imports `next.conf` and is still half-executed when this module loads.
    return common.errors_for_unknown_keys(raw, allowed=allowed, prefix="NEXT_FRAMEWORK")


__all__ = ["check_next_framework_unknown_top_level_keys"]
