"""System checks for the configuration layer.

The ids are `next.E035` for an unknown key, `next.E076` and `next.W072` for a value the
merge would discard, and `next.E077` for a non-dict `NEXT_FRAMEWORK`.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT, common

from .defaults import USER_SETTING
from .merge import OPTIONAL_STR_KEYS
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


# next.forms and next.partial carry their own raw per-key checks for these.
_TYPED_LIST_KEYS: frozenset[str] = NextFrameworkSettings.LIST_KEYS - {
    "FORM_ACTION_BACKENDS",
    "FORM_ANCHOR_FILES",
    "PARTIAL_BACKENDS",
}
_TYPED_OPTIONAL_KEYS: frozenset[str] = OPTIONAL_STR_KEYS - {"JS_CONTEXT_SERIALIZER"}
_KEY_TYPES: dict[str, type] = (
    dict.fromkeys(sorted(_TYPED_LIST_KEYS), list)
    | dict.fromkeys(sorted(NextFrameworkSettings.STR_KEYS), str)
    | dict.fromkeys(sorted(_TYPED_OPTIONAL_KEYS), str)
    | {"NEXT_JS_OPTIONS": dict}
)

_SILENCE_HINT = (
    "Fix the value in settings.NEXT_FRAMEWORK, or silence this check by "
    "adding its id to SILENCED_SYSTEM_CHECKS."
)


@register(NEXT)
def check_next_framework_value_types(*args, **kwargs) -> list[CheckMessage]:
    """Report `NEXT_FRAMEWORK` values whose type the merge would silently drop.

    A non-dict `NEXT_FRAMEWORK` skips the per-key probes and carries its own id, so
    silencing one mistyped key never silences "the whole setting is ignored".
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
                id="next.E077",
            )
        ]
    if not raw:
        return []
    return _wrong_type_errors(raw) + _non_bool_warnings(raw)


def _survives_merge(key: str, value: object) -> bool:
    """Report whether one raw value reaches the merged settings under its key."""
    if value is None and key in _TYPED_OPTIONAL_KEYS:
        return True
    return isinstance(value, _KEY_TYPES[key])


def _wrong_type_errors(raw: dict[str, Any]) -> list[CheckMessage]:
    """Report every key whose value type the settings merge would drop."""
    return [
        Error(
            f"NEXT_FRAMEWORK[{key!r}] must be a {expected.__name__}, "
            f"got {type(raw[key]).__name__!r}. The settings merge "
            "ignores this value and silently keeps the framework default.",
            hint=_SILENCE_HINT,
            obj=settings,
            id="next.E076",
        )
        for key, expected in _KEY_TYPES.items()
        if key in raw and not _survives_merge(key, raw[key])
    ]


def _non_bool_warnings(raw: dict[str, Any]) -> list[CheckMessage]:
    """Report every bool flag holding a value `bool()` would coerce."""
    return [
        DjangoWarning(
            f"NEXT_FRAMEWORK[{key!r}] should be a bool, got "
            f"{type(raw[key]).__name__!r}. The bool() coercion turns "
            "falsy-looking strings such as 'False' into True.",
            hint=_SILENCE_HINT,
            obj=settings,
            id="next.W072",
        )
        for key in sorted(NextFrameworkSettings.BOOL_KEYS)
        if key in raw and not isinstance(raw[key], bool)
    ]


__all__ = [
    "check_next_framework_unknown_top_level_keys",
    "check_next_framework_value_types",
]
