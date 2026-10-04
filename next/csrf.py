"""Where a page puts the CSRF token for its runtime and forms, and the token endpoint.

A token in the HTML sets the CSRF cookie, so a page a shared cache may store defers it.
"""

import enum
import functools
import logging
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
from django.utils.cache import add_never_cache_headers, patch_vary_headers

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.diagnostics import FailureLog
from next.site.headers import ROBOTS_HEADER


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)


CSRF_URL_NAME: Final = "csrf"
"""The name the token endpoint answers under in the `next` namespace."""

CSRF_DEFERRED_ATTR: Final = "_next_csrf_deferred"
"""The request attribute that marks a render whose HTML carries no CSRF token."""

_REQUEST_FLAG: Final = "X-Next-Request"
_SAFE_METHODS: Final = ("GET", "HEAD")
_SAME_ORIGIN: Final = "same-origin"


class CsrfDelivery(enum.StrEnum):
    """Where a rendered page puts the CSRF token its forms and runtime post with.

    `AUTO` defers it on a page a shared cache may store and embeds it on the rest.
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
    """Keep the token out of the HTML of this render. The runtime fetches it instead."""
    setattr(request, CSRF_DEFERRED_ATTR, True)


def token_deferred(request: HttpRequest) -> bool:
    """Whether this render keeps the CSRF token out of its HTML."""
    return getattr(request, CSRF_DEFERRED_ATTR, False) is True


def csrf_header_name() -> str:
    """Return the CSRF header name in HTTP wire form from Django settings.

    Django stores `CSRF_HEADER_NAME` in WSGI `META` form, so it is converted to the
    wire name with the rule `request.headers` applies.
    """
    raw = settings.CSRF_HEADER_NAME
    name = HttpHeaders.parse_header_name(raw)
    if name is not None:
        return name
    return raw.replace("_", "-").title()


def csrf_token_payload(request: HttpRequest) -> dict[str, str]:
    """Return the header name and a newly masked token, creating the secret if none."""
    return {"header": csrf_header_name(), "token": get_token(request)}


def csrf_url() -> str:
    """Return the token endpoint, under the `next` namespace or bare as the root."""
    try:
        return reverse(f"next:{CSRF_URL_NAME}")
    except NoReverseMatch:
        return reverse(CSRF_URL_NAME)


def csrf_payload(request: HttpRequest) -> dict[str, str]:
    """Return the `$csrf` payload, the endpoint URL in place of a deferred token.

    When the endpoint is not routed the token is embedded instead, so forms still post.
    """
    if token_deferred(request):
        try:
            return {"header": csrf_header_name(), "url": csrf_url()}
        except NoReverseMatch as exc:
            _failures.contain(
                exc,
                CSRF_URL_NAME,
                "The CSRF token endpoint does not reverse, so this page embeds its "
                "token instead of deferring it. Add path('', include('next.urls')) "
                "to ROOT_URLCONF.",
            )
    return csrf_token_payload(request)


def _refusal(request: HttpRequest) -> HttpResponse | None:
    """Return the error response for a request that gets no token, else `None`."""
    if request.method not in _SAFE_METHODS:
        return HttpResponseNotAllowed(_SAFE_METHODS)
    if request.headers.get(_REQUEST_FLAG) != "1":
        return HttpResponseBadRequest(f"{_REQUEST_FLAG}: 1 is required")
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None and fetch_site != _SAME_ORIGIN:
        return HttpResponseForbidden("Only a same-origin request gets a token")
    return None


def csrf_view(request: HttpRequest) -> HttpResponse:
    """Return a new token to the runtime of a page whose HTML carries none.

    The required custom header forces a CORS preflight, so no other origin reads it.
    """
    refusal = _refusal(request)
    response = JsonResponse(csrf_token_payload(request)) if refusal is None else refusal
    add_never_cache_headers(response)
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
