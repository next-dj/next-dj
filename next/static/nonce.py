"""Resolve the CSP nonce of a request once, for every tag the framework renders."""

import functools
from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.http import HttpRequest

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.utils import UNSET, middleware_listed


NONCE_ATTR: Final = "_next_csp_nonce"
"""The request attribute that holds the nonce resolved for the request."""

CSP_NONCE_ATTR: Final = "_csp_nonce"
"""The request attribute where Django's CSP middleware and django-csp keep the nonce."""

CSP_MIDDLEWARE: Final = (
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "csp.middleware.CSPMiddleware",
)
"""The middleware classes that mint a nonce per request, Django 6.0+ and django-csp."""

type NonceResolver = Callable[[HttpRequest], object]

django_get_nonce: NonceResolver | None
try:
    from django.middleware.csp import get_nonce as django_get_nonce
except ImportError:  # pragma: no cover - Django before 6.0 mints no nonce itself
    django_get_nonce = None


def request_nonce(request: HttpRequest) -> str | None:
    """Return the nonce django-csp or Django's CSP middleware minted for `request`.

    Reading the lazy nonce generates it, so the middleware then writes it into the
    `Content-Security-Policy` header.
    """
    value = getattr(request, "csp_nonce", None)
    if value is None and django_get_nonce is not None:
        value = django_get_nonce(request)
    return None if value is None else str(value) or None


@functools.cache
def nonce_enabled() -> bool:
    """Return whether `CSP_NONCE` allows framework tags to carry the request nonce."""
    return bool(next_framework_settings.CSP_NONCE)


def nonce_active() -> bool:
    """Return whether tags get a nonce, which needs `CSP_NONCE` and a CSP middleware."""
    if not nonce_enabled():
        return False
    middleware = getattr(settings, "MIDDLEWARE", None) or ()
    return any(middleware_listed(middleware, path) for path in CSP_MIDDLEWARE)


def forget_nonce_setting(**kwargs) -> None:
    """Clear the memoised `CSP_NONCE` value, so a settings reload takes effect."""
    nonce_enabled.cache_clear()


settings_reloaded.connect(forget_nonce_setting)


def resolve_nonce(request: HttpRequest | None) -> str | None:
    """Return the nonce of `request`, resolved once and memoised on the request.

    A nonce belongs to one visitor, so `nonce_minted` later makes the response private.
    """
    if not isinstance(request, HttpRequest):
        return None
    held = getattr(request, NONCE_ATTR, UNSET)
    if held is not UNSET:
        return held if isinstance(held, str) else None
    nonce = request_nonce(request) if nonce_enabled() else None
    setattr(request, NONCE_ATTR, nonce)
    return nonce


def nonce_minted(request: HttpRequest) -> bool:
    """Return whether a nonce was generated for `request`, by a framework tag or not.

    Django's `LazyNonce` is truthy once evaluated, and django-csp stores the string.
    """
    return isinstance(getattr(request, NONCE_ATTR, None), str) or bool(
        getattr(request, CSP_NONCE_ATTR, None)
    )


__all__ = [
    "CSP_MIDDLEWARE",
    "CSP_NONCE_ATTR",
    "NONCE_ATTR",
    "NonceResolver",
    "forget_nonce_setting",
    "nonce_active",
    "nonce_enabled",
    "nonce_minted",
    "request_nonce",
    "resolve_nonce",
]
