"""Where a page puts its CSRF token, the token endpoint, and the request render flags.

A token in the HTML sets the CSRF cookie, so a page a shared cache may store defers it.
"""

import enum
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
from django.middleware.csrf import CSRF_SESSION_KEY, get_token
from django.urls import (
    NoReverseMatch,
    get_resolver,
    get_script_prefix,
    get_urlconf,
    reverse,
)
from django.utils import translation
from django.utils.cache import add_never_cache_headers, patch_vary_headers

from next.caches import FRAMEWORK, BoundedCache, memo
from next.conf import next_framework_settings
from next.conf.sentinels import UNSET, Unset
from next.diagnostics import FailureLog
from next.permissions.access import open_view
from next.requests import held_next, next_of
from next.site.headers import ROBOTS_HEADER
from next.utils import request_header
from next.wire import REQUEST_FLAG


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)


CSRF_URL_NAME: Final = "csrf"
"""The name the token endpoint answers under in the `next` namespace."""

_COOKIE_KEY: Final = "CSRF_COOKIE"
_RENEWED_KEY: Final = "CSRF_COOKIE_NEEDS_UPDATE"
TOKEN_META: Final = (_COOKIE_KEY, _RENEWED_KEY)
"""The `META` keys a token read sets, which tell the middleware to send the cookie."""
_SAFE_METHODS: Final = ("GET", "HEAD")
_SAME_ORIGIN: Final = "same-origin"


class CsrfDelivery(enum.StrEnum):
    """Where a rendered page puts the CSRF token its forms and runtime post with.

    `AUTO` embeds it only where the render read a token or the page streams.
    """

    AUTO = "auto"
    EAGER = "eager"
    LAZY = "lazy"


@memo(FRAMEWORK)
def csrf_delivery() -> CsrfDelivery:
    """Return the `CSRF_DELIVERY` setting, `AUTO` where it names no mode."""
    try:
        return CsrfDelivery(next_framework_settings.CSRF_DELIVERY)
    except ValueError:
        return CsrfDelivery.AUTO


def defer_token(request: HttpRequest) -> None:
    """Keep the token out of the HTML of this render. The runtime fetches it instead."""
    next_of(request).csrf_deferred = True


def token_deferred(request: HttpRequest) -> bool:
    """Whether this render keeps the CSRF token out of its HTML."""
    held = held_next(request)
    return held is not None and held.csrf_deferred


CSP_NONCE_ATTR: Final = "_csp_nonce"
"""The request attribute where Django's CSP middleware and django-csp keep the nonce."""


def memoised_nonce(request: HttpRequest) -> str | Unset | None:
    """Return the nonce memoised for `request`, `UNSET` before one was resolved."""
    held = held_next(request)
    return UNSET if held is None else held.nonce


def memoise_nonce(request: HttpRequest, nonce: str | None) -> None:
    """Memoise the nonce resolved for `request`, where `None` names no nonce."""
    next_of(request).nonce = nonce


def nonce_minted(request: HttpRequest) -> bool:
    """Return whether a nonce was generated for `request`, by a framework tag or not.

    Django's `LazyNonce` is truthy once evaluated, and django-csp stores the string.
    """
    return isinstance(memoised_nonce(request), str) or bool(
        getattr(request, CSP_NONCE_ATTR, None)
    )


def csrf_header_name() -> str:
    """Return `CSRF_HEADER_NAME` in wire form, converted as `request.headers` does."""
    raw = settings.CSRF_HEADER_NAME
    name = HttpHeaders.parse_header_name(raw)
    if name is not None:
        return name
    return raw.replace("_", "-").title()


def csrf_token_payload(request: HttpRequest) -> dict[str, str]:
    """Return the header name and a newly masked token, creating the secret if none."""
    return {"header": csrf_header_name(), "token": get_token(request)}


_urls: Final[BoundedCache[tuple[str | None, str, str | None], tuple[object, str]]] = (
    BoundedCache(depends=(FRAMEWORK,))
)
"""The reversed endpoint per URLconf, script prefix and language, with its resolver.

`clear_url_caches()` drops the resolver whenever the routes change, so a held resolver
that is not the current one marks a stale address.
"""


def csrf_url() -> str:
    """Return the token endpoint, reversed once per URLconf, script prefix and language.

    Under a root `next.urls` there is no namespace, so `csrf_view` is addressed itself.
    """
    urlconf = get_urlconf()
    resolver = get_resolver(urlconf)
    key = (urlconf, get_script_prefix(), translation.get_language())
    held = _urls.get(key)
    if held is not None and held[0] is resolver:
        return held[1]
    try:
        url = reverse(f"next:{CSRF_URL_NAME}")
    except NoReverseMatch:
        url = reverse(csrf_view)
    _urls[key] = (resolver, url)
    return url


def _endpoint() -> dict[str, str] | None:
    """Return the payload naming the endpoint, `None` while it does not reverse.

    A cookie scripts may read is named too, so the client sends it before asking.
    """
    with _failures.guard(
        CSRF_URL_NAME,
        "The CSRF token endpoint does not reverse, so this page embeds its token "
        "instead of deferring it. Add path('', include('next.urls')) to "
        "ROOT_URLCONF.",
        catch=(NoReverseMatch,),
    ):
        payload = {"header": csrf_header_name(), "url": csrf_url()}
        if not (settings.CSRF_COOKIE_HTTPONLY or settings.CSRF_USE_SESSIONS):
            payload["cookie"] = settings.CSRF_COOKIE_NAME
        return payload
    return None


def csrf_payload(request: HttpRequest) -> dict[str, str]:
    """Return the `$csrf` payload, the endpoint URL in place of a deferred token.

    When the endpoint is not routed the token is embedded instead, so forms still post.
    One request masks one token per secret, shared by its page and its envelope.
    """
    if token_deferred(request):
        endpoint = _endpoint()
        if endpoint is not None:
            return endpoint
    meta = request.META
    namespace = next_of(request)
    held = namespace.csrf_payload
    if held is not None and held[0] == meta.get(_COOKIE_KEY):
        return held[1]
    payload = csrf_token_payload(request)
    namespace.csrf_payload = (meta.get(_COOKIE_KEY), payload)
    return payload


def token_minted(request: HttpRequest) -> bool:
    """Whether this request already read a token, so its response sets the cookie."""
    return bool(request.META.get(_RENEWED_KEY))


def document_csrf(
    request: HttpRequest | None, *, streams: bool
) -> dict[str, str] | None:
    """Return the `$csrf` payload of a rendered page, `None` without a real request.

    Under `AUTO` a page that minted no token names the endpoint, as Django sets the
    cookie only once a template reads one. A streamed page mints before its headers.
    """
    if request is None or not isinstance(getattr(request, "META", None), dict):
        return None
    if (
        not streams
        and csrf_delivery() is CsrfDelivery.AUTO
        and not token_minted(request)
    ):
        endpoint = _endpoint()
        if endpoint is not None:
            return endpoint
    return csrf_payload(request)


def secret_changed(request: HttpRequest) -> bool:
    """Whether the response sets a CSRF secret other than the one the request brought.

    Reading a token flags the cookie for renewal too, so the flag alone names no change.
    """
    meta = request.META
    if not meta.get(_RENEWED_KEY):
        return False
    if settings.CSRF_USE_SESSIONS:
        brought = request.session.get(CSRF_SESSION_KEY)
    else:
        brought = request.COOKIES.get(settings.CSRF_COOKIE_NAME)
    return bool(brought != meta.get(_COOKIE_KEY))


def _refusal(request: HttpRequest) -> HttpResponse | None:
    """Return the error response for a request that gets no token, else `None`."""
    if request.method not in _SAFE_METHODS:
        return HttpResponseNotAllowed(_SAFE_METHODS)
    if request_header(request, REQUEST_FLAG) != "1":
        return HttpResponseBadRequest(f"{REQUEST_FLAG}: 1 is required")
    fetch_site = request_header(request, "Sec-Fetch-Site")
    if fetch_site is not None and fetch_site != _SAME_ORIGIN:
        return HttpResponseForbidden("Only a same-origin request gets a token")
    return None


@open_view
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
    "CSP_NONCE_ATTR",
    "CSRF_URL_NAME",
    "TOKEN_META",
    "CsrfDelivery",
    "csrf_delivery",
    "csrf_header_name",
    "csrf_payload",
    "csrf_token_payload",
    "csrf_url",
    "csrf_view",
    "defer_token",
    "document_csrf",
    "memoise_nonce",
    "memoised_nonce",
    "nonce_minted",
    "secret_changed",
    "token_deferred",
    "token_minted",
]
