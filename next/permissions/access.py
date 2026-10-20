"""The access a page or a component declares and the one check every adapter runs.

The check returns a denial rather than a response, so each adapter answers in its form.
"""

import functools
from collections.abc import Callable, Iterable
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponseRedirect, QueryDict
from django.http.response import HttpResponseBase
from django.shortcuts import resolve_url
from django.utils.module_loading import import_string

from next.conf import next_framework_settings
from next.utils import is_middleware, redirect_or_fallback

from .markers import AccessObject, AccessRule, Denial, Requirement
from .registry import DeclarationRegistry


if TYPE_CHECKING:
    from django.contrib.auth.middleware import LoginRequiredMiddleware


_LOGIN_MIDDLEWARE = "django.contrib.auth.middleware.LoginRequiredMiddleware"

_AUTH_APP: Final = "django.contrib.auth"

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


def require_login(source: Path, *, inherit: bool = False) -> None:
    """Bind to `source` the requirement of a logged-in user, as `login_required`."""
    declarations.declare(source, Requirement("login", source), inherit=inherit)


def require_permission(
    source: Path,
    perm: str | tuple[str, ...],
    obj: AccessObject | None,
    *,
    inherit: bool = False,
) -> None:
    """Bind to `source` the requirement of every permission in `perm`, on `obj`."""
    requirement = Requirement("permission", source, _permissions(perm), obj)
    declarations.declare(source, requirement, inherit=inherit)


def declare_public(source: Path, *, inherit: bool = False) -> None:
    """Mark `source` public, lifting what the files above require of it."""
    declarations.declare_public(source, inherit=inherit)


def declared(file_path: Path) -> AccessRule:
    """Return the access `file_path` declares about itself, open when it has none."""
    return declarations.declared(file_path)


def declared_files() -> tuple[Path, ...]:
    """Return every file that holds an access declaration."""
    return declarations.files()


def signed_in(user: object) -> bool:
    """Return whether `user` is authenticated, `False` for a request without a user."""
    return bool(getattr(user, "is_authenticated", False))


class _Unnamed(Enum):
    """The absent object of a permission asked without one."""

    TOKEN = auto()


UNNAMED: Final = _Unnamed.TOKEN
"""The object of a permission asked of the model, not of one row."""


def holds(user: object, perms: Iterable[str], obj: object = UNNAMED) -> bool:
    """Return whether `user` holds every one of `perms`, on `obj` when one is named.

    A named object that is `None` denies, so it never asks the model question instead.
    """
    has_perm = getattr(user, "has_perm", None)
    if not callable(has_perm) or obj is None:
        return False
    if obj is UNNAMED:
        return all(map(has_perm, perms))
    return all(has_perm(perm, obj) for perm in perms)


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
    for requirement in requirements:
        if requirement.obj is not None or not requirement.perms:
            continue
        if not holds(user, requirement.perms):
            return Denial("permission", requirement)
    for requirement in requirements:
        if requirement.obj is None:
            continue
        if lookup is None or not holds(
            user, requirement.perms, lookup(requirement.obj)
        ):
            return Denial("permission", requirement)
    return None


def auth_installed() -> bool:
    """Return whether `django.contrib.auth` is installed, by any `AppConfig` path."""
    return apps.is_installed(_AUTH_APP)


def _no_view(request: HttpRequest) -> HttpResponseBase:
    """Stand in for the view a middleware hook takes, where no URL resolved one."""
    del request
    raise Http404


def login_middleware() -> str | None:
    """Return the `MIDDLEWARE` entry naming `LoginRequiredMiddleware` or a subclass."""
    if not auth_installed():
        return None
    listed = (entry for entry in settings.MIDDLEWARE if isinstance(entry, str))
    return next(
        (entry for entry in listed if is_middleware(entry, _LOGIN_MIDDLEWARE)), None
    )


@functools.cache
def _listed_middleware() -> "LoginRequiredMiddleware | None":
    """Return the listed login middleware, built the way the request handler builds it.

    Its own login URL and field name then shape a redirect the framework answers.
    """
    entry = login_middleware()
    if entry is None:
        return None
    built = import_string(entry)(_no_view)
    return cast("LoginRequiredMiddleware", built)


@functools.cache
def login_by_default() -> bool:
    """Return whether an endpoint that declares nothing and is not public asks a login.

    The `LOGIN_REQUIRED` setting turns it on, and so does `LoginRequiredMiddleware`.
    """
    if next_framework_settings.LOGIN_REQUIRED:
        return True
    return login_middleware() is not None


def login_redirect(request: HttpRequest, next_url: str) -> HttpResponseRedirect:
    """Redirect to the login page with `next_url`, as `LoginRequiredMiddleware` would.

    A listed subclass names the URL and the field, another host gets an absolute next.
    """
    middleware = _listed_middleware()
    login_url = settings.LOGIN_URL
    field = REDIRECT_FIELD_NAME
    if middleware is not None:
        view = getattr(request.resolver_match, "func", _no_view)
        login_url = middleware.get_login_url(view)
        field = middleware.get_redirect_field_name(view)
    resolved = resolve_url(login_url)
    scheme, netloc, path, query, fragment = urlsplit(resolved)
    if scheme or netloc:
        here = urlsplit(request.build_absolute_uri(next_url))
        if (scheme and scheme != here.scheme) or (netloc and netloc != here.netloc):
            next_url = here.geturl()
    if field:
        querystring = QueryDict(query, mutable=True)
        querystring[field] = next_url
        query = querystring.urlencode(safe="/")
    target = (scheme, netloc, path, query, fragment)
    return redirect_or_fallback(urlunsplit(target), resolved)


def answer(denial: Denial, request: HttpRequest, next_url: str) -> HttpResponseRedirect:
    """Return the login redirect a login denial owes, raising a 403 for any other."""
    if denial.reason == "login":
        return login_redirect(request, next_url)
    raise PermissionDenied


class _OpenView(Protocol):
    """A framework view open to everyone, which `manage.py permissions` reads off."""

    next_open: bool


def open_view[V: Callable[..., HttpResponseBase]](view: V) -> V:
    """Exempt a framework view from the login middleware and mark it open to everyone.

    `login_not_required` alone tells nothing of what the view itself still refuses.
    """
    exempt = login_not_required(view)
    cast("_OpenView", exempt).next_open = True
    return exempt


def forget_login_default(*, setting: str, **kwargs: object) -> None:
    """Drop the memoised answers once a setting the default login reads changes."""
    if setting in {"NEXT_FRAMEWORK", "MIDDLEWARE", "INSTALLED_APPS"}:
        login_by_default.cache_clear()
        _listed_middleware.cache_clear()


__all__ = [
    "Lookup",
    "answer",
    "auth_installed",
    "check",
    "declarations",
    "declare_public",
    "declared",
    "declared_files",
    "forget_login_default",
    "holds",
    "login_by_default",
    "login_middleware",
    "login_redirect",
    "open_view",
    "require_login",
    "require_permission",
    "signed_in",
]
