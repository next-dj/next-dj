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
_UNRESOLVED: Final = "unresolved"


def _directive_name(directive: str) -> str:
    """Return the lower-cased name of one `Cache-Control` directive."""
    return directive.split("=", 1)[0].strip().lower()


def _shared(response: HttpResponseBase, names: set[str]) -> bool:
    """Whether a shared cache may store `response` under its current headers."""
    if not _SHARED_DIRECTIVES.isdisjoint(names):
        return True
    if any(name in response for name in CDN_HEADERS):
        return True
    if not _PRIVATE_DIRECTIVES.isdisjoint(names):
        return False
    return "max-age" in names or "Expires" in response


def _route(request: HttpRequest) -> str:
    """Return the name of the route `request` resolved to, a constant when none."""
    match = getattr(request, "resolver_match", None)
    return _UNRESOLVED if match is None else match.view_name


def guard_shared_cache(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase:
    """Make `response` private when it sets a cookie that a shared cache would store."""
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

    List it first in `MIDDLEWARE`, or directly below `UpdateCacheMiddleware`.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Return `response`, private when it sets a cookie under a shared cache."""
        return guard_shared_cache(request, response)


__all__ = ["SharedCacheGuardMiddleware", "guard_shared_cache"]
