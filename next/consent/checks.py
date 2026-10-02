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

from .backends import ConsentBackend
from .manager import CONSENT_KEYS
from .markers import NECESSARY


_PREFIX: Final = "NEXT_FRAMEWORK['CONSENT']"

_CATEGORY_NAME: Final = re.compile(r"[A-Za-z0-9_.-]+")
"""A category name the consent cookie `2:<a>|<b>:<seconds>` carries intact.

`:` closes the list and `|` separates the names, so neither may appear in one, nor
`,`, which separated them in the first format the server still reads.
"""


def _raw_categories() -> object:
    """Return `CONSENT['CATEGORIES']` as written, `None` where it is not."""
    scope = raw_scope("CONSENT")
    return None if scope is None else scope.get("CATEGORIES")


def category_list_problem() -> str | None:
    """Return what makes `CONSENT['CATEGORIES']` unusable, `None` when nothing does.

    The scripts checks read it too, so a list `next.E135` reports draws no `next.E140`
    for every script it would otherwise leave out.
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
    """Report a `CONSENT['CATEGORIES']` that is no list of names or lacks necessary."""
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
        problem = "is no dotted path"
    else:
        try:
            backend = import_class_cached(path)
        except ImportError as exc:
            problem = f"does not import: {exc}"
        else:
            if not (isinstance(backend, type) and issubclass(backend, ConsentBackend)):
                problem = "is no next.consent.ConsentBackend subclass"
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
    "check_consent_cookie_secure",
    "check_consent_settings",
]
