"""The check of the keys and value shapes the `DEFER` scope is written in.

It also reads the `DEFER` paths the other suspense checks name in their messages.
"""

from collections.abc import Mapping
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import raw_scope
from next.conf.checks import error_for_misfit
from next.conf.defer import SCOPE, SHAPE, long_token_age
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.shapes import Misfit, misfits


_TOKEN_KEYS: Final = ("TOKENS", "OPTIONS", "KEYS")
_TOKEN_LIFETIME: Final = frozenset(
    {("TOKENS", "OPTIONS", "MAX_AGE"), ("TOKENS", "OPTIONS", "BUCKET")}
)
_PREFIX: Final = f"NEXT_FRAMEWORK[{SCOPE!r}]"


def value_at(scope: Mapping[str, object], path: tuple[str, ...]) -> object:
    """Return the value under `path`, `None` where a level is no mapping."""
    value: object = scope
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def shipped(path: tuple[str, ...]) -> object:
    """Return the value the framework ships under `path` of `DEFER`."""
    return value_at(FROZEN_DEFAULTS[SCOPE], path)


def where(path: tuple[str, ...]) -> str:
    """Return how a message names the `DEFER` key under `path`."""
    return _PREFIX + "".join(f"[{key!r}]" for key in path)


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


def _message(misfit: Misfit) -> CheckMessage:
    """Build the `next.E035`, `next.E076` or `next.E233` error for a `DEFER` misfit."""
    if misfit.path == _TOKEN_KEYS or misfit.path in _TOKEN_LIFETIME:
        where = "".join(f"[{key!r}]" for key in misfit.path)
        return _token_error(_PREFIX + where, misfit)
    return error_for_misfit(_PREFIX, misfit)


@register(NEXT)
def check_defer_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys and the value shapes of the `DEFER` scope, at every level.

    The `OPTIONS` of a backend other than the shipped one belong to that backend.
    """
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    messages = [
        _message(misfit) for misfit in misfits(scope, SHAPE, FROZEN_DEFAULTS[SCOPE])
    ]
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


__all__ = ["check_defer_settings", "shipped", "value_at", "where"]
