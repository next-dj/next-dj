"""System checks for `NEXT_FRAMEWORK["CONSENT"]`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import errors_for_unknown_keys, raw_scope
from next.conf import import_class_cached
from next.conf.defaults import AUTO
from next.utils import is_int

from .backends import ConsentBackend, CookieConsentBackend
from .manager import CONSENT_KEYS
from .markers import NECESSARY


_PREFIX: Final = "NEXT_FRAMEWORK['CONSENT']"

_SAMESITE: Final = frozenset({"lax", "strict", "none"})
"""The `SameSite` values a browser accepts, compared without case as Django does."""

_CATEGORY_NAME: Final = re.compile(r"[A-Za-z0-9_.-]+")
"""A category name the consent cookie `2:<a>|<b>:<seconds>` carries intact.

`:` ends the list and `|` separates the names, so a name contains neither. A name
also excludes `,`, the separator of format `1`, which the server still reads.
"""


def _raw_categories() -> object:
    """Return `CONSENT['CATEGORIES']` as written, `None` where it is not."""
    scope = raw_scope("CONSENT")
    return None if scope is None else scope.get("CATEGORIES")


def category_list_problem() -> str | None:
    """Return what makes `CONSENT['CATEGORIES']` unusable, `None` when nothing does.

    The scripts checks call it too, so a list `next.E135` reports raises no `next.E140`
    for each script as well.
    """
    listed = _raw_categories()
    if listed is None:
        return None
    if not isinstance(listed, list | tuple) or not all(
        isinstance(name, str) and name for name in listed
    ):
        return "lists no category names. Write names like ['necessary', 'ads']"
    if NECESSARY not in listed:
        return (
            f"does not list {NECESSARY!r}, the category every visitor grants. "
            f"Add {NECESSARY!r} to the list"
        )
    return None


def _listed_categories() -> list[CheckMessage]:
    """Report a `CONSENT['CATEGORIES']` that lists no names or lacks necessary."""
    problem = category_list_problem()
    if problem is None:
        return []
    return [Error(f"{_PREFIX}['CATEGORIES'] {problem}.", obj=settings, id="next.E135")]


def _unsafe_categories() -> list[CheckMessage]:
    """Report a category name the consent cookie cannot carry (`next.E146`)."""
    listed = _raw_categories()
    names = listed if isinstance(listed, list | tuple) else ()
    return [
        Error(
            f"{_PREFIX}['CATEGORIES'] lists {name!r}, which the consent cookie cannot "
            "carry, since it writes the granted names between colons, separated by "
            "'|'. Use only letters, digits, '_', '-' and '.'.",
            obj=settings,
            id="next.E146",
        )
        for name in names
        if isinstance(name, str) and name and not _CATEGORY_NAME.fullmatch(name)
    ]


def _backend_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    path = scope.get("BACKEND")
    if path is None:
        return []
    problem: str | None = None
    if not isinstance(path, str) or not path:
        problem = "is not a dotted path"
    else:
        try:
            backend = import_class_cached(path)
        except ImportError as exc:
            problem = f"does not import: {exc}"
        else:
            if not (isinstance(backend, type) and issubclass(backend, ConsentBackend)):
                problem = "is not a next.consent.ConsentBackend subclass"
    if problem is None:
        return []
    return [
        Error(
            f"{_PREFIX}['BACKEND'] {path!r} {problem}, so every page treats each "
            "visitor as undecided and denies every category but necessary. Name a "
            "next.consent.ConsentBackend subclass by its dotted path, or remove "
            "BACKEND to read the cookie the runtime writes.",
            obj=settings,
            id="next.E137",
        )
    ]


def _server_render_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    value = scope.get("SERVER_RENDER", AUTO)
    if isinstance(value, bool) or value == AUTO:
        return []
    return [
        Error(
            f"{_PREFIX}['SERVER_RENDER'] is {value!r}. Write 'auto', True or False.",
            obj=settings,
            id="next.E145",
        )
    ]


@register(NEXT)
def check_consent_settings(*args, **kwargs) -> list[CheckMessage]:
    """Validate the keys (E035), the render mode (E145) and the backend (E137)."""
    scope = raw_scope("CONSENT")
    if scope is None:
        return []
    return [
        *errors_for_unknown_keys(dict(scope), allowed=CONSENT_KEYS, prefix=_PREFIX),
        *_server_render_errors(scope),
        *_backend_errors(scope),
    ]


@register(NEXT)
def check_consent_categories(*args, **kwargs) -> list[CheckMessage]:
    """Report an unusable category list (E135) and a name the cookie cannot carry."""
    return _listed_categories() + _unsafe_categories()


def _cookie_options(scope: Mapping[str, object]) -> Mapping[str, object] | None:
    """Return the `OPTIONS` of a cookie backend, `None` under any other backend.

    A backend that does not import is reported by `next.E137` alone.
    """
    path = scope.get("BACKEND")
    if path is not None:
        try:
            backend = import_class_cached(path) if isinstance(path, str) else None
        except ImportError:
            return None
        cookie = isinstance(backend, type) and issubclass(backend, CookieConsentBackend)
        if not cookie:
            return None
    options = scope.get("OPTIONS")
    return options if isinstance(options, Mapping) else {}


def _samesite_errors(options: Mapping[str, object]) -> list[CheckMessage]:
    """Report an unknown `samesite` (E150), or `'None'` without `secure` (W125)."""
    samesite = options.get("samesite")
    if samesite is None:
        return []
    if not isinstance(samesite, str) or samesite.lower() not in _SAMESITE:
        return [
            Error(
                f"{_PREFIX}['OPTIONS']['samesite'] is {samesite!r}, which the browser "
                "ignores, so the consent cookie takes its default SameSite policy. "
                "Write 'Lax', 'Strict' or 'None', or remove it.",
                obj=settings,
                id="next.E150",
            )
        ]
    if samesite.lower() == "none" and options.get("secure") is not True:
        return [
            DjangoWarning(
                f"{_PREFIX}['OPTIONS']['samesite'] is 'None' while 'secure' is not "
                "True, so a browser rejects the consent cookie on every page that is "
                "not served over https and the choice is not kept. Set 'secure' to "
                "True, or write 'Lax'.",
                obj=settings,
                id="next.W125",
            )
        ]
    return []


def _max_age_errors(options: Mapping[str, object]) -> list[CheckMessage]:
    """Report a `max_age` that is not a positive number of seconds (E151)."""
    max_age = options.get("max_age")
    if max_age is None or (is_int(max_age) and max_age > 0):
        return []
    return [
        Error(
            f"{_PREFIX}['OPTIONS']['max_age'] is {max_age!r}. A value that is not an "
            "int is replaced by the default age, and a value of zero or less makes "
            "the browser drop the consent cookie at once, so the choice is not kept. "
            "Write a positive number of seconds, or remove it.",
            obj=settings,
            id="next.E151",
        )
    ]


@register(NEXT)
def check_consent_cookie_options(*args, **kwargs) -> list[CheckMessage]:
    """Validate the cookie `samesite` (E150, W125) and `max_age` (E151) options."""
    scope = raw_scope("CONSENT")
    options = None if scope is None else _cookie_options(scope)
    if options is None:
        return []
    return [*_samesite_errors(options), *_max_age_errors(options)]


@register(NEXT, deploy=True)
def check_consent_cookie_secure(*args, **kwargs) -> list[CheckMessage]:
    """Warn when the session cookie is Secure and the consent cookie is not (W119)."""
    if not getattr(settings, "SESSION_COOKIE_SECURE", False):
        return []
    scope = raw_scope("CONSENT") or {}
    options = scope.get("OPTIONS")
    if not isinstance(options, Mapping) or options.get("secure") is not False:
        return []
    return [
        DjangoWarning(
            f"{_PREFIX}['OPTIONS']['secure'] is False while SESSION_COOKIE_SECURE is "
            "True, so the consent cookie also travels over plain HTTP. Leave it None "
            "to follow the scheme, or set it True.",
            obj=settings,
            id="next.W119",
        )
    ]


__all__ = [
    "category_list_problem",
    "check_consent_categories",
    "check_consent_cookie_options",
    "check_consent_cookie_secure",
    "check_consent_settings",
]
