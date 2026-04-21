"""System checks for the configuration layer."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import CheckMessage, Tags, register

from next.checks.common import errors_for_unknown_keys

from .settings import NextFrameworkSettings


@register(Tags.compatibility)
def check_next_framework_unknown_top_level_keys(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Reject keys under `NEXT_FRAMEWORK` that are not defined in defaults."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is None or not isinstance(raw, dict):
        return []
    allowed = frozenset(NextFrameworkSettings.DEFAULTS.keys())
    return errors_for_unknown_keys(
        raw,
        allowed=allowed,
        prefix="NEXT_FRAMEWORK",
    )


__all__ = ["check_next_framework_unknown_top_level_keys"]
