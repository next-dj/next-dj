"""How a page hands the CSRF token to its runtime and forms, and the token endpoint.

A token in the HTML sets a cookie, so a page a shared cache holds defers it instead.
"""

import enum
import functools
from typing import Final

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.http.request import HttpHeaders
from django.middleware.csrf import get_token
from django.urls import NoReverseMatch, reverse
from django.utils.cache import patch_vary_headers

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.site.headers import ROBOTS_HEADER


CSRF_URL_NAME: Final = "csrf"
"""The name the token endpoint answers under in the `next` namespace."""

CSRF_DEFERRED_ATTR: Final = "_next_csrf_deferred"
"""The request attribute that marks a render whose HTML carries no CSRF token."""

_REQUEST_FLAG: Final = "X-Next-Request"
_SAFE_METHODS: Final = ("GET", "HEAD")
_SAME_ORIGIN: Final = "same-origin"


class CsrfDelivery(enum.StrEnum):
    """Where a rendered page puts the CSRF token its forms and runtime post with.

    `AUTO` defers it on a page a shared cache holds and embeds it everywhere else.
    """

    AUTO = "auto"
    EAGER = "eager"
    LAZY = "lazy"


@functools.cache
def csrf_delivery() -> CsrfDelivery:
    """Return the `CSRF_DELIVERY` setting, `AUTO` where it names no mode."""
    try:
        return CsrfDelivery(next_framework_settings.CSRF_DELIVERY)
    except ValueError:
        return CsrfDelivery.AUTO


def forget_csrf_delivery(**kwargs) -> None:
    """Drop the memoised delivery mode, so a settings reload takes effect."""
    csrf_delivery.cache_clear()


settings_reloaded.connect(forget_csrf_delivery)


def defer_token(request: HttpRequest) -> None:
    """Keep the token of this render out of its HTML, the runtime fetches it instead."""
    setattr(request, CSRF_DEFERRED_ATTR, True)


def token_deferred(request: HttpRequest) -> bool:
    """Whether this render keeps the CSRF token out of its HTML."""
    return getattr(request, CSRF_DEFERRED_ATTR, False) is True


def csrf_header_name() -> str:
    """Return the CSRF header name in HTTP wire form from Django settings.

    Django stores `CSRF_HEADER_NAME` in WSGI `META` form, so it is unmangled to the
    wire name with the same rule Django uses to expose headers.
    """
    raw = settings.CSRF_HEADER_NAME
    name = HttpHeaders.parse_header_name(raw)
    if name is not None:
        return name
    return raw.removeprefix(HttpHeaders.HTTP_PREFIX).replace("_", "-").title()


def csrf_token_payload(request: HttpRequest) -> dict[str, str]:
    """Return the header name and a freshly masked token, minting one when needed."""
    return {"header": csrf_header_name(), "token": get_token(request)}


def csrf_url() -> str:
    """Return the token endpoint, under the `next` namespace or bare as the root."""
    try:
        return reverse(f"next:{CSRF_URL_NAME}")
    except NoReverseMatch:
        return reverse(CSRF_URL_NAME)


def csrf_payload(request: HttpRequest) -> dict[str, str]:
    """Return the `$csrf` payload, the endpoint URL in place of a deferred token."""
    if token_deferred(request):
        return {"header": csrf_header_name(), "url": csrf_url()}
    return csrf_token_payload(request)


def _refusal(request: HttpRequest) -> HttpResponse | None:
    """Return the answer to a request the endpoint must not hand a token to."""
    if request.method not in _SAFE_METHODS:
        return HttpResponseNotAllowed(_SAFE_METHODS)
    if request.headers.get(_REQUEST_FLAG) != "1":
        return HttpResponseBadRequest(f"{_REQUEST_FLAG}: 1 is required")
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None and fetch_site != _SAME_ORIGIN:
        return HttpResponseForbidden("Only a same-origin request gets a token")
    return None


def csrf_view(request: HttpRequest) -> HttpResponse:
    """Answer the runtime of a page whose HTML carries no token with a fresh one.

    The custom request header forces a CORS preflight, so no other site reads it.
    """
    refusal = _refusal(request)
    response = JsonResponse(csrf_token_payload(request)) if refusal is None else refusal
    response["Cache-Control"] = "private, no-store"
    patch_vary_headers(response, ("Cookie",))
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = _SAME_ORIGIN
    response[ROBOTS_HEADER] = "noindex"
    return response


__all__ = [
    "CSRF_DEFERRED_ATTR",
    "CSRF_URL_NAME",
    "CsrfDelivery",
    "csrf_delivery",
    "csrf_header_name",
    "csrf_payload",
    "csrf_token_payload",
    "csrf_url",
    "csrf_view",
    "defer_token",
    "forget_csrf_delivery",
    "token_deferred",
]
