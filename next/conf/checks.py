"""System checks for the configuration layer."""

from __future__ import annotations

from typing import Any, Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT

from .defaults import USER_SETTING
from .defer import SCOPE as DEFER_SCOPE, Misfit, long_token_age, misfits
from .merge import OPTIONAL_STR_KEYS, UNSET, accepted_value
from .settings import NextFrameworkSettings


def errors_for_unknown_keys(
    config: dict[str, Any], *, allowed: frozenset[str], prefix: str
) -> list[CheckMessage]:
    """Return an `Error` list when `config` contains keys outside `allowed`."""
    unknown = sorted(k for k in config if k not in allowed)
    if not unknown:
        return []
    unknown_fmt = ", ".join(repr(k) for k in unknown)
    allowed_fmt = ", ".join(sorted(allowed))
    return [
        Error(
            f"{prefix} has unknown keys {unknown_fmt}. Allowed keys are {allowed_fmt}.",
            obj=settings,
            id="next.E035",
        )
    ]


@register(NEXT)
def check_next_framework_unknown_top_level_keys(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Reject keys under `NEXT_FRAMEWORK` that are not defined in defaults."""
    raw = getattr(settings, USER_SETTING, None)
    if raw is None or not isinstance(raw, dict):
        return []
    allowed = frozenset(NextFrameworkSettings.DEFAULTS.keys())
    return errors_for_unknown_keys(raw, allowed=allowed, prefix="NEXT_FRAMEWORK")


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
    | {
        "COMPRESSION": dict,
        "CONSENT": dict,
        "DEFER": dict,
        "METADATA": dict,
        "NEXT_JS_OPTIONS": dict,
        "REALTIME": dict,
        "ROUTER": dict,
        "SEO": dict,
        "SITE": dict,
        "TRANSACTIONS": dict,
    }
)

_SILENCE_HINT = (
    "Fix the value in settings.NEXT_FRAMEWORK, or silence this check by "
    "adding its id to SILENCED_SYSTEM_CHECKS."
)


@register(NEXT)
def check_next_framework_value_types(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Report `NEXT_FRAMEWORK` values whose type the merge would silently drop.

    A non-dict `NEXT_FRAMEWORK` skips the per-key probes and carries its own id.
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


def _wrong_type_errors(raw: dict[str, Any]) -> list[CheckMessage]:
    """Report every key whose value type the settings merge would drop.

    Asked of the merge itself, so the check and the merge cannot drift apart.
    """
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
        if key in raw and accepted_value(key, raw[key]) is UNSET
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


def error_for_wrong_shape(prefix: str, expected: str, found: str) -> CheckMessage:
    """Return the `next.E076` error for a nested settings value outside its shape."""
    return Error(
        f"{prefix} must be {expected}, got {found}. The framework ignores this value "
        "and keeps its default.",
        hint="Write the value in the shape named, or drop the key for its default.",
        obj=settings,
        id="next.E076",
    )


def error_for_unusable_path(
    prefix: str, dotted: str, problem: str, consequence: str, hint: str
) -> CheckMessage:
    """Return the `next.E219` error for a settings path that names nothing usable."""
    return Error(
        f"{prefix} {dotted!r} {problem}, so {consequence}.",
        hint=hint,
        obj=settings,
        id="next.E219",
    )


def error_for_unusable_seam(
    prefix: str, dotted: str, problem: str, consequence: str, hint: str
) -> CheckMessage:
    """Return the `next.E237` error for a router or transaction path that names nothing.

    The router seams and the transaction rule share it, so the id lives in one place.
    """
    return Error(
        f"{prefix} {dotted!r} {problem}, so {consequence}.",
        hint=hint,
        obj=settings,
        id="next.E237",
    )


_TOKEN_KEYS: Final = ("TOKENS", "OPTIONS", "KEYS")
_TOKEN_LIFETIME: Final = frozenset(
    {("TOKENS", "OPTIONS", "MAX_AGE"), ("TOKENS", "OPTIONS", "BUCKET")}
)


def _token_error(prefix: str, misfit: Misfit) -> CheckMessage:
    """Build the `next.E233` error for a token key or lifetime outside its shape."""
    if misfit.path == _TOKEN_KEYS:
        why = "A short key lets anyone forge reload tokens"
        hint = "Generate each key with secrets.token_urlsafe(32) or drop KEYS."
    else:
        why = "A token whose bucket reaches its lifetime is born expired"
        hint = "Keep MAX_AGE from 1 and BUCKET below MAX_AGE, or drop BUCKET."
    return Error(
        f"{prefix} must be {misfit.expected}, got {misfit.found}. {why}, so the "
        "framework ignores this value and keeps its default.",
        hint=hint,
        obj=settings,
        id="next.E233",
    )


def _defer_message(misfit: Misfit) -> CheckMessage:
    """Build the `next.E035`, `next.E076` or `next.E233` error for a `DEFER` misfit."""
    where = "".join(f"[{key!r}]" for key in (DEFER_SCOPE, *misfit.path))
    prefix = f"NEXT_FRAMEWORK{where}"
    if misfit.path == _TOKEN_KEYS or misfit.path in _TOKEN_LIFETIME:
        return _token_error(prefix, misfit)
    if misfit.unknown:
        unknown = ", ".join(repr(key) for key in misfit.unknown)
        return Error(
            f"{prefix} has unknown keys {unknown}. Allowed keys are "
            f"{', '.join(misfit.allowed)}.",
            obj=settings,
            id="next.E035",
        )
    return error_for_wrong_shape(prefix, misfit.expected, misfit.found)


@register(NEXT)
def check_defer_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys and the value shapes of the `DEFER` scope, at every level.

    The `OPTIONS` of a backend other than the shipped one belong to that backend.
    """
    raw = getattr(settings, USER_SETTING, None)
    scope = raw.get(DEFER_SCOPE) if isinstance(raw, dict) else None
    if not isinstance(scope, dict):
        return []
    messages = [_defer_message(misfit) for misfit in misfits(scope)]
    age = long_token_age(scope)
    if age is not None:
        messages.append(
            DjangoWarning(
                f"NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']['MAX_AGE'] is {age} "
                "seconds, above one day. A leaked reload token stays usable that long.",
                hint="Keep MAX_AGE at 86400 or below.",
                obj=settings,
                id="next.W175",
            )
        )
    return messages


__all__ = [
    "check_defer_settings",
    "check_next_framework_unknown_top_level_keys",
    "check_next_framework_value_types",
    "error_for_unusable_path",
    "error_for_unusable_seam",
    "error_for_wrong_shape",
    "errors_for_unknown_keys",
]
