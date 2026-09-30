"""The CSP nonce every tag the framework writes carries, read once per request."""

import functools
import importlib
from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.http import HttpRequest

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.pages.responses import mark_personal_render
from next.utils import UNSET


NONCE_ATTR: Final = "_next_csp_nonce"
"""The request attribute the nonce of one render is held under."""

CSP_MIDDLEWARE: Final = (
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "csp.middleware.CSPMiddleware",
)
"""The middleware classes minting a nonce per request, Django 6.0 and django-csp."""

type NonceResolver = Callable[[HttpRequest], object]


@functools.cache
def django_get_nonce() -> NonceResolver | None:
    """Return Django's own `get_nonce`, `None` before Django 6.0 ships one."""
    try:
        module = importlib.import_module("django.middleware.csp")
    except ImportError:
        return None
    found = getattr(module, "get_nonce", None)
    return found if callable(found) else None


def request_nonce(request: HttpRequest) -> str | None:
    """Return the nonce django-csp or Django's CSP middleware minted for `request`.

    Reading it mints it, so the middleware writes it into the header it sends.
    """
    value = getattr(request, "csp_nonce", None)
    if value is None:
        get_nonce = django_get_nonce()
        value = None if get_nonce is None else get_nonce(request)
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
    return any(path in middleware for path in CSP_MIDDLEWARE)


def forget_nonce_setting(**kwargs) -> None:
    """Drop the memoised switch, so a settings reload takes effect."""
    nonce_enabled.cache_clear()


settings_reloaded.connect(forget_nonce_setting)


def resolve_nonce(request: HttpRequest | None) -> str | None:
    """Return the nonce of this render, read once per request.

    A nonce is one visitor's, so the render goes personal and no shared cache keeps it.
    """
    if not isinstance(request, HttpRequest):
        return None
    held = getattr(request, NONCE_ATTR, UNSET)
    if held is not UNSET:
        return held if isinstance(held, str) else None
    nonce = request_nonce(request) if nonce_enabled() else None
    setattr(request, NONCE_ATTR, nonce)
    if nonce is not None:
        mark_personal_render(request)
    return nonce


__all__ = [
    "CSP_MIDDLEWARE",
    "NONCE_ATTR",
    "NonceResolver",
    "django_get_nonce",
    "forget_nonce_setting",
    "nonce_active",
    "nonce_enabled",
    "request_nonce",
    "resolve_nonce",
]
