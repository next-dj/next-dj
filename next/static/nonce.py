"""Resolve the CSP nonce of a request once, for every tag the framework renders."""

from collections.abc import Callable
from typing import Final

from django.http import HttpRequest

from next.caches import FRAMEWORK, memo
from next.conf import next_framework_settings
from next.conf.sentinels import UNSET
from next.csrf import CSP_NONCE_ATTR, memoise_nonce, memoised_nonce, nonce_minted
from next.utils import middleware_installed


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

    Reading the lazy nonce mints it, so the middleware writes it into the CSP header.
    """
    value = getattr(request, "csp_nonce", None)
    if value is None and django_get_nonce is not None:
        value = django_get_nonce(request)
    return None if value is None else str(value) or None


@memo(FRAMEWORK)
def nonce_enabled() -> bool:
    """Return whether `CSP_NONCE` allows framework tags to carry the request nonce."""
    return bool(next_framework_settings.CSP_NONCE)


def nonce_active() -> bool:
    """Return whether tags get a nonce, which needs `CSP_NONCE` and a CSP middleware."""
    if not nonce_enabled():
        return False
    return any(map(middleware_installed, CSP_MIDDLEWARE))


def resolve_nonce(request: HttpRequest | None) -> str | None:
    """Return the nonce of `request`, resolved once and memoised on the request.

    A nonce belongs to one visitor, so `nonce_minted` later makes the response private.
    """
    if not isinstance(request, HttpRequest):
        return None
    held = memoised_nonce(request)
    if held is not UNSET:
        return held
    nonce = request_nonce(request) if nonce_enabled() else None
    memoise_nonce(request, nonce)
    return nonce


__all__ = [
    "CSP_MIDDLEWARE",
    "CSP_NONCE_ATTR",
    "NonceResolver",
    "nonce_active",
    "nonce_enabled",
    "nonce_minted",
    "request_nonce",
    "resolve_nonce",
]
