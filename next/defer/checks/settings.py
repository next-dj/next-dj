"""The check of the keys and value shapes the `DEFER` scope is written in.

It also reads the `DEFER` paths the other checks of the area name in their messages.
"""

from collections.abc import Mapping
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning

from next.checks import NEXT, register
from next.checks.common import raw_scope
from next.conf.checks import error_for_misfit
from next.conf.defer import SCOPE, SHAPE, long_token_age
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.shapes import Misfit, misfits


_TOKEN_LIFETIME: Final = ("TOKENS", "OPTIONS", "MAX_AGE")
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
    """Build the `next.E233` error for a token lifetime outside its shape."""
    return Error(
        f"{prefix} must be {misfit.expected}, got {misfit.found}. A token without a "
        "lifetime is born expired, so the first read of DEFER raises "
        "ImproperlyConfigured for it.",
        hint="Keep MAX_AGE an integer from 1 or the dotted path of a callable.",
        obj=settings,
        id="next.E233",
    )


def _message(misfit: Misfit) -> CheckMessage:
    """Build the `next.E035`, `next.E076` or `next.E233` error for a `DEFER` misfit."""
    if misfit.path == _TOKEN_LIFETIME:
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
