"""The CSP nonce every tag the framework writes carries, read once per request."""

import functools
from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.http import HttpRequest

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.utils import UNSET, middleware_listed


NONCE_ATTR: Final = "_next_csp_nonce"
"""The request attribute the nonce of one render is held under."""

CSP_NONCE_ATTR: Final = "_csp_nonce"
"""Where Django's CSP middleware and django-csp keep the nonce of a request."""

CSP_MIDDLEWARE: Final = (
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "csp.middleware.CSPMiddleware",
)
"""The middleware classes minting a nonce per request, Django 6.0 and django-csp."""

type NonceResolver = Callable[[HttpRequest], object]

django_get_nonce: NonceResolver | None
try:
    from django.middleware.csp import get_nonce as django_get_nonce
except ImportError:  # pragma: no cover - Django before 6.0 mints no nonce itself
    django_get_nonce = None


def request_nonce(request: HttpRequest) -> str | None:
    """Return the nonce django-csp or Django's CSP middleware minted for `request`.

    Reading it mints it, so the middleware writes it into the header it sends.
    """
    value = getattr(request, "csp_nonce", None)
    if value is None and django_get_nonce is not None:
        value = django_get_nonce(request)
    return None if value is None else str(value) or None


@functools.cache
def nonce_enabled() -> bool:
    """Whether `CSP_NONCE` lets the framework tags carry the nonce of a request."""
    return bool(next_framework_settings.CSP_NONCE)


def nonce_active() -> bool:
    """Whether renders hand the tags a nonce, `CSP_NONCE` on and a minter installed."""
    if not nonce_enabled():
        return False
    middleware = getattr(settings, "MIDDLEWARE", None) or ()
    return any(middleware_listed(middleware, path) for path in CSP_MIDDLEWARE)


def forget_nonce_setting(**kwargs) -> None:
    """Drop the memoised switch, so a settings reload takes effect."""
    nonce_enabled.cache_clear()


settings_reloaded.connect(forget_nonce_setting)


def resolve_nonce(request: HttpRequest | None) -> str | None:
    """Return the nonce of this render, read once per request.

    A nonce is one visitor's, so `nonce_minted` takes the response private afterwards.
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
    """Whether a nonce was minted for `request`, by a framework tag or anyone else.

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
