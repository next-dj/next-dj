"""System checks for the configuration layer.

Unknown top-level keys are reported as `next.E035`. A non-dict
`NEXT_FRAMEWORK` and values whose type the settings merge would
silently discard are reported as `next.E076`, and non-bool values for
bool flags as `next.W072`.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT, common

from .defaults import USER_SETTING
from .settings import NextFrameworkSettings


@register(NEXT)
def check_next_framework_unknown_top_level_keys(*args, **kwargs) -> list[CheckMessage]:
    """Reject keys under `NEXT_FRAMEWORK` that are not defined in defaults."""
    raw = getattr(settings, USER_SETTING, None)
    if raw is None or not isinstance(raw, dict):
        return []
    allowed = frozenset(NextFrameworkSettings.DEFAULTS.keys())
    # The module is imported rather than the name, because `next.checks.common`
    # imports `next.conf` and is still half-executed when this module loads.
    return common.errors_for_unknown_keys(raw, allowed=allowed, prefix="NEXT_FRAMEWORK")


# Keys with their own raw per-key checks (FORM_ACTION_BACKENDS, FORM_ANCHOR_FILES,
# FORM_WIZARD_BACKEND, JS_CONTEXT_SERIALIZER) are excluded to avoid duplicate
# messages on one key.
_TYPED_LIST_KEYS: frozenset[str] = NextFrameworkSettings._LIST_KEYS - {
    "FORM_ACTION_BACKENDS",
    "FORM_ANCHOR_FILES",
}
_KEY_TYPES: dict[str, type] = dict.fromkeys(sorted(_TYPED_LIST_KEYS), list) | {
    "URL_NAME_TEMPLATE": str,
    "URL_RESOLVER": str,
    "NEXT_JS_OPTIONS": dict,
}

_SILENCE_HINT = (
    "Fix the value in settings.NEXT_FRAMEWORK, or silence this check by "
    "adding its id to SILENCED_SYSTEM_CHECKS."
)


@register(NEXT)
def check_next_framework_value_types(*args, **kwargs) -> list[CheckMessage]:
    """Report `NEXT_FRAMEWORK` values whose type the merge would silently drop.

    A non-dict `NEXT_FRAMEWORK` is reported as `next.E076` on its own
    and skips the per-key probes, which have nothing to index into.
    """
    raw = getattr(settings, USER_SETTING, None)
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return [
            Error(
                f"NEXT_FRAMEWORK must be a dict, got {type(raw).__name__!r}. "
                "The settings layer ignores a non-dict value entirely and "
                "uses the framework defaults.",
                hint=_SILENCE_HINT,
                obj=settings,
                id="next.E076",
            )
        ]
    if not raw:
        return []
    messages: list[CheckMessage] = []
    for key, expected in _KEY_TYPES.items():
        if key in raw and not isinstance(raw[key], expected):
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK[{key!r}] must be a {expected.__name__}, "
                    f"got {type(raw[key]).__name__!r}. The settings merge "
                    "ignores this value and silently keeps the framework "
                    "default.",
                    hint=_SILENCE_HINT,
                    obj=settings,
                    id="next.E076",
                )
            )
    messages.extend(
        DjangoWarning(
            f"NEXT_FRAMEWORK[{key!r}] should be a bool, got "
            f"{type(raw[key]).__name__!r}. The bool() coercion turns "
            "falsy-looking strings such as 'False' into True.",
            hint=_SILENCE_HINT,
            obj=settings,
            id="next.W072",
        )
        for key in sorted(NextFrameworkSettings._BOOL_KEYS)
        if key in raw and not isinstance(raw[key], bool)
    )
    return messages


__all__ = [
    "check_next_framework_unknown_top_level_keys",
    "check_next_framework_value_types",
]
