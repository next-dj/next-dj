"""The access a page, proxy or component declares and the one check every adapter runs.

The check returns a denial rather than a response, so each adapter answers in its form.
"""

import functools
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect, QueryDict
from django.shortcuts import resolve_url

from next.introspect import registering_file
from next.utils import is_middleware, redirect_or_fallback

from .markers import AccessObject, AccessRule, Denial, Requirement
from .registry import DeclarationRegistry


_LOGIN_MIDDLEWARE = "django.contrib.auth.middleware.LoginRequiredMiddleware"

_AUTH_APP = "django.contrib.auth"

declarations = DeclarationRegistry()

type Lookup = Callable[[AccessObject], object]


def _permissions(perm: object) -> tuple[str, ...]:
    """Return `perm` as a tuple of permission names, the forms `has_perms` accepts."""
    names = (perm,) if isinstance(perm, str) else perm
    if not isinstance(names, tuple | list) or not names:
        msg = "permission_required() takes a permission name or a tuple of names."
        raise TypeError(msg)
    if not all(isinstance(name, str) and name for name in names):
        msg = "permission_required() takes only non-empty permission names."
        raise TypeError(msg)
    return tuple(names)


class Declarations:
    """The login a page, proxy or component module requires of its requester.

    Each binds to the file that calls it, read from the stack like `@context` reads it.
    """

    def login_required(self) -> None:
        """Require a logged-in user, the way Django's `login_required` does."""
        source = registering_file()
        declarations.declare(source, Requirement("login", source))


def require_permission(
    source: Path, perm: str | tuple[str, ...], obj: AccessObject | None
) -> None:
    """Bind to `source` the requirement of every permission in `perm`, on `obj`."""
    requirement = Requirement("permission", source, _permissions(perm), obj)
    declarations.declare(source, requirement)


def declared(file_path: Path) -> AccessRule:
    """Return the access `file_path` declares about itself, open when it has none."""
    return declarations.declared(file_path)


def declared_files() -> tuple[Path, ...]:
    """Return every file that holds an access declaration."""
    return declarations.files()


def signed_in(user: object) -> bool:
    """Return whether `user` is authenticated, `False` for a request without a user."""
    return bool(getattr(user, "is_authenticated", False))


def check(
    user: object, rule: AccessRule, lookup: Lookup | None = None
) -> Denial | None:
    """Return why `user` may not reach an endpoint under `rule`, `None` when it may.

    The order is `PermissionRequiredMixin`, so objects load after the other permissions.
    """
    requirements = rule.requirements
    if not requirements:
        return None
    if not signed_in(user):
        return Denial("login", requirements[0])
    has_perm = getattr(user, "has_perm", None)
    for requirement in requirements:
        if requirement.obj is not None or not requirement.perms:
            continue
        if has_perm is None or not all(map(has_perm, requirement.perms)):
            return Denial("permission", requirement)
    for requirement in requirements:
        if requirement.obj is None:
            continue
        if lookup is None or has_perm is None:
            return Denial("permission", requirement)
        target = lookup(requirement.obj)
        if not all(has_perm(perm, target) for perm in requirement.perms):
            return Denial("permission", requirement)
    return None


def login_redirect(next_url: str) -> HttpResponseRedirect:
    """Redirect to `LOGIN_URL` with `next_url`, dropped past Django's Location cap.

    It mirrors `redirect_to_login`, whose module needs `contrib.auth` installed.
    """
    login_url = resolve_url(settings.LOGIN_URL)
    scheme, netloc, path, query, fragment = urlsplit(login_url)
    querystring = QueryDict(query, mutable=True)
    querystring[REDIRECT_FIELD_NAME] = next_url
    target = (scheme, netloc, path, querystring.urlencode(safe="/"), fragment)
    return redirect_or_fallback(urlunsplit(target), login_url)


def answer(denial: Denial, next_url: str) -> HttpResponseRedirect:
    """Return the login redirect a login denial owes, raising a 403 for any other."""
    if denial.reason == "login":
        return login_redirect(next_url)
    raise PermissionDenied


def login_middleware() -> str | None:
    """Return the `MIDDLEWARE` entry naming `LoginRequiredMiddleware` or a subclass."""
    if not apps.is_installed(_AUTH_APP):
        return None
    listed = (entry for entry in settings.MIDDLEWARE if isinstance(entry, str))
    return next(
        (entry for entry in listed if is_middleware(entry, _LOGIN_MIDDLEWARE)), None
    )


@functools.cache
def login_middleware_active() -> bool:
    """Return whether `MIDDLEWARE` lists `LoginRequiredMiddleware` or a subclass."""
    return login_middleware() is not None


def forget_login_middleware(*, setting: str, **kwargs: object) -> None:
    """Drop the memoised answer once `MIDDLEWARE` or `INSTALLED_APPS` changes."""
    if setting in {"MIDDLEWARE", "INSTALLED_APPS"}:
        login_middleware_active.cache_clear()


__all__ = [
    "Declarations",
    "Lookup",
    "answer",
    "check",
    "declarations",
    "declared",
    "declared_files",
    "forget_login_middleware",
    "login_middleware",
    "login_middleware_active",
    "login_redirect",
    "require_permission",
    "signed_in",
]
