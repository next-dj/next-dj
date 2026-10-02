"""The middleware that keeps a response carrying a cookie out of every shared cache."""

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
"""The headers a CDN reads ahead of `Cache-Control`, so a private answer drops them."""

_WARNED: BoundedCache[str, bool] = BoundedCache()


def _forget_warnings(**kwargs) -> None:
    """Re-arm the warning of every path, so a reconfigure is reported afresh."""
    _WARNED.clear()


settings_reloaded.connect(_forget_warnings)


def _directive_name(directive: str) -> str:
    return directive.split("=", 1)[0].strip().lower()


def _shared(response: HttpResponseBase, names: set[str]) -> bool:
    """Whether a shared cache may keep `response` as its headers stand.

    `public` and `s-maxage` say so outright, and a CDN header speaks to the CDN
    alone. Without `private` or `no-store`, a freshness lifetime from `max-age` or
    `Expires` lets a shared cache store it too, as `cache_page` emits it.
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
    """Take `response` private when it sets a cookie a shared cache would hand out.

    `public` and `s-maxage` leave `Cache-Control`, `private` leads it, the CDN
    headers go, and `Vary` gains `Cookie`. A path is reported once, since every hit
    repeats the same cause.
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
            "SharedCacheGuardMiddleware sent it private. Find the view or middleware "
            "that sets the cookie, or drop the shared cache of that response.",
            request.path,
        )
    return response


class SharedCacheGuardMiddleware(MiddlewareMixin):
    """Send private every response that sets a cookie while a shared cache may keep it.

    A page takes its own cache private when a cookie lands, but a third-party
    middleware or a 304 answered above the page can still pair the two. List it first
    in `MIDDLEWARE`, below `UpdateCacheMiddleware` alone, so it sees every cookie.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Return `response`, private when it sets a cookie under a shared cache."""
        return guard_shared_cache(request, response)


__all__ = ["SharedCacheGuardMiddleware", "guard_shared_cache"]
