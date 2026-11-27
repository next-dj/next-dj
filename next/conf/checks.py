"""System checks for the configuration layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning

from next.checks import NEXT, register

from . import boundaries
from .defaults import USER_SETTING
from .imports import callable_problem
from .merge import OPTIONAL_STR_KEYS, UNSET, WARNED_KEYS, accepted_value
from .settings import NextFrameworkSettings
from .shapes import misfits, text


if TYPE_CHECKING:
    from collections.abc import Iterable

    from .shapes import Misfit


def errors_for_unknown_keys(
    config: dict[str, Any], *, allowed: frozenset[str], prefix: str
) -> list[CheckMessage]:
    """Return an `Error` list when `config` contains keys outside `allowed`."""
    unknown = sorted(k for k in config if k not in allowed)
    if not unknown:
        return []
    return [_unknown_keys_error(prefix, unknown, sorted(allowed))]


def _unknown_keys_error(
    prefix: str, unknown: Iterable[object], allowed: Iterable[str]
) -> CheckMessage:
    """Build the `next.E035` error naming the unknown keys and the allowed ones."""
    unknown_fmt = ", ".join(repr(key) for key in unknown)
    allowed_fmt = ", ".join(allowed)
    return Error(
        f"{prefix} has unknown keys {unknown_fmt}. Allowed keys are {allowed_fmt}.",
        obj=settings,
        id="next.E035",
    )


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
_TYPED_OPTIONAL_KEYS: frozenset[str] = OPTIONAL_STR_KEYS - WARNED_KEYS
_KEY_TYPES: dict[str, type] = (
    dict.fromkeys(sorted(_TYPED_LIST_KEYS), list)
    | dict.fromkeys(sorted(NextFrameworkSettings.STR_KEYS), str)
    | dict.fromkeys(sorted(_TYPED_OPTIONAL_KEYS), str)
    | {
        "BOUNDARIES": dict,
        "COMPRESSION": dict,
        "CONSENT": dict,
        "DEFER": dict,
        "LOADER": dict,
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
    """Report `NEXT_FRAMEWORK` values whose type the merge refuses.

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
    """Report every key whose value type the settings merge refuses.

    Asked of the merge itself, so the check and the merge cannot drift apart.
    """
    return [
        Error(
            f"NEXT_FRAMEWORK[{key!r}] must be a {expected.__name__}, "
            f"got {type(raw[key]).__name__!r}. The settings keep its default and "
            "every request raises ImproperlyConfigured until it is fixed.",
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
        f"{prefix} must be {expected}, got {found}. The first read of the scope "
        "raises ImproperlyConfigured for it.",
        hint="Write the value in the shape named, or drop the key for its default.",
        obj=settings,
        id="next.E076",
    )


def error_for_misfit(prefix: str, misfit: Misfit) -> CheckMessage:
    """Return the `next.E035` or `next.E076` error for one misfit below `prefix`."""
    where = prefix + "".join(f"[{key!r}]" for key in misfit.path)
    if misfit.unknown:
        return _unknown_keys_error(where, misfit.unknown, misfit.allowed)
    return error_for_wrong_shape(where, misfit.expected, misfit.found)


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


_BOUNDARIES: Final = f"NEXT_FRAMEWORK[{boundaries.SCOPE!r}]"


@register(NEXT)
def check_boundaries_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys, the value shapes and the `REPORTER` path of `BOUNDARIES`."""
    raw = getattr(settings, USER_SETTING, None)
    scope = raw.get(boundaries.SCOPE) if isinstance(raw, dict) else None
    if not isinstance(scope, dict):
        return []
    errors = [
        error_for_misfit(_BOUNDARIES, misfit)
        for misfit in misfits(scope, boundaries.SHAPE)
    ]
    reporter = scope.get("REPORTER")
    problem = callable_problem(reporter) if text(reporter) else None
    if problem is not None:
        errors.append(
            error_for_unusable_path(
                f"{_BOUNDARIES}['REPORTER']",
                str(reporter),
                problem,
                "the first failure a boundary contains raises ImproperlyConfigured",
                "Name an importable callable.",
            )
        )
    return errors


__all__ = [
    "check_boundaries_settings",
    "check_next_framework_unknown_top_level_keys",
    "check_next_framework_value_types",
    "error_for_misfit",
    "error_for_unusable_path",
    "error_for_wrong_shape",
    "errors_for_unknown_keys",
]
