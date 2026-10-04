"""Middleware that makes a response setting a cookie private for every shared cache."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from django.utils.cache import patch_vary_headers
from django.utils.deprecation import MiddlewareMixin

from next.caches import BoundedCache
from next.conf.signals import settings_reloaded


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http.response import HttpResponseBase


logger = logging.getLogger(__name__)

_SHARED_DIRECTIVES: Final = frozenset({"public", "s-maxage"})
_PRIVATE_DIRECTIVES: Final = frozenset({"private", "no-store"})
_DROPPED_DIRECTIVES: Final = _SHARED_DIRECTIVES | {"private"}
_CDN_HEADERS: Final = (
    "CDN-Cache-Control",
    "Cloudflare-CDN-Cache-Control",
    "Surrogate-Control",
)
"""The headers a CDN reads in place of `Cache-Control`, removed when made private."""

_WARNED: BoundedCache[str, bool] = BoundedCache()


def _forget_warnings(**kwargs) -> None:
    """Clear the warned paths, so a path is logged again after a reconfiguration."""
    _WARNED.clear()


settings_reloaded.connect(_forget_warnings)


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
    if any(name in response for name in _CDN_HEADERS):
        return True
    if not _PRIVATE_DIRECTIVES.isdisjoint(names):
        return False
    return "max-age" in names or "Expires" in response


def guard_shared_cache(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase:
    """Make `response` private when it sets a cookie that a shared cache would store.

    `public` and `s-maxage` are removed from `Cache-Control` and `private` is put first,
    the CDN headers are removed, and `Vary` gains `Cookie`. Each path is logged once,
    since every later request has the same cause.
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
    for name in _CDN_HEADERS:
        response.headers.pop(name, None)
    patch_vary_headers(response, ("Cookie",))
    if request.path not in _WARNED:
        _WARNED[request.path] = True
        logger.warning(
            "The response to %s set a cookie under a shared Cache-Control, so "
            "SharedCacheGuardMiddleware made it private. Find the view or middleware "
            "that sets the cookie, or drop the shared cache of that response.",
            request.path,
        )
    return response


class SharedCacheGuardMiddleware(MiddlewareMixin):
    """Make private every response that sets a cookie while a shared cache may store it.

    A page makes its own cache private when a cookie is set, but a third-party
    middleware or a 304 built above the page can still combine the two. List it first
    in `MIDDLEWARE`, or directly below `UpdateCacheMiddleware`, so it sees every cookie.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Return `response`, private when it sets a cookie under a shared cache."""
        return guard_shared_cache(request, response)


__all__ = ["SharedCacheGuardMiddleware", "guard_shared_cache"]
