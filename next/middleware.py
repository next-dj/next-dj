"""Middleware that makes a response setting a cookie private for every shared cache."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from django.utils.cache import patch_vary_headers
from django.utils.deprecation import MiddlewareMixin

from next.diagnostics import FailureLog
from next.pages.responses import CDN_HEADERS, drop_cdn_headers


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http.response import HttpResponseBase


logger = logging.getLogger(__name__)
_failures = FailureLog(logger)

_SHARED_DIRECTIVES: Final = frozenset({"public", "s-maxage"})
_PRIVATE_DIRECTIVES: Final = frozenset({"private", "no-store"})
_DROPPED_DIRECTIVES: Final = _SHARED_DIRECTIVES | {"private"}


def _directive_name(directive: str) -> str:
    """Return the lower-cased name of one `Cache-Control` directive."""
    return directive.split("=", 1)[0].strip().lower()


def _shared(response: HttpResponseBase, names: set[str]) -> bool:
    """Whether a shared cache may store `response` under its current headers.

    `public`, `s-maxage` and a CDN header permit it explicitly. Without `private` or
    `no-store`, a freshness lifetime from `max-age` or `Expires` permits it too, which
    is the form `cache_page` emits.
    """
    if not _SHARED_DIRECTIVES.isdisjoint(names):
        return True
    if any(name in response for name in CDN_HEADERS):
        return True
    if not _PRIVATE_DIRECTIVES.isdisjoint(names):
        return False
    return "max-age" in names or "Expires" in response


def _route(request: HttpRequest) -> str:
    """Return the URL pattern `request` resolved to, or its path when it resolved none.

    A dynamic route serves many paths for one cause, so the warning is keyed by route.
    """
    match = getattr(request, "resolver_match", None)
    return request.path if match is None else match.route


def guard_shared_cache(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase:
    """Make `response` private when it sets a cookie that a shared cache would store.

    `public` and `s-maxage` are removed from `Cache-Control` and `private` is put first,
    the CDN headers are removed, and `Vary` gains `Cookie`. The warning is keyed by
    route and logged at the `FailureLog` rate, since every request to a route has the
    same cause.
    """
    if not response.cookies:
        return response
    directives = [
        part.strip()
        for part in response.get("Cache-Control", "").split(",")
        if part.strip()
    ]
    names = {_directive_name(part) for part in directives}
    if not _shared(response, names):
        return response
    kept = [
        part for part in directives if _directive_name(part) not in _DROPPED_DIRECTIVES
    ]
    response["Cache-Control"] = ", ".join(("private", *kept))
    drop_cdn_headers(response)
    patch_vary_headers(response, ("Cookie",))
    _failures.warn(
        _route(request),
        "The response to %s set a cookie under a shared Cache-Control, so "
        "SharedCacheGuardMiddleware made it private. Find the view or middleware "
        "that sets the cookie, or drop the shared cache of that response.",
        request.path,
    )
    return response


class SharedCacheGuardMiddleware(MiddlewareMixin):
    """Make private every response that sets a cookie while a shared cache may store it.

    A page makes its own response private when a cookie is set, but a third-party
    middleware or a 304 built above the page can still combine the two. List it first
    in `MIDDLEWARE`, or directly below `UpdateCacheMiddleware`, so it sees every cookie.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Return `response`, private when it sets a cookie under a shared cache."""
        return guard_shared_cache(request, response)


__all__ = ["SharedCacheGuardMiddleware", "guard_shared_cache"]
