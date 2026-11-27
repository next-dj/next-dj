"""The router clear on login, logout and a cycled session, and its directives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.contrib.sessions.backends.signed_cookies import SessionStore as SignedStore

from next.conf.router import router_enabled
from next.partial.headers import ROUTER
from next.router.signals import router_cleared

from .state import Routing


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http.response import HttpResponseBase


CLEAR: Final = "clear"
"""The `X-Next-Router` directive that flushes every prefetch and snapshot."""

NO_SNAPSHOT: Final = "no_snapshot"
"""The `X-Next-Router` directive that keeps a page out of history snapshots."""

_CLEAR_ATTR: Final = "_next_router_clear"


def router_clear(request: HttpRequest, reason: str = "policy") -> None:
    """Tell the client router to flush every prefetch and snapshot on this response.

    Login, logout and a session key cycle send it on their own.
    """
    if getattr(request, _CLEAR_ATTR, None) is not None:
        return
    setattr(request, _CLEAR_ATTR, reason)
    if router_cleared.has_listeners(Routing):
        router_cleared.send_robust(sender=Routing, reason=reason, request=request)


def clear_on_login(*, request: HttpRequest, **kwargs: object) -> None:
    """Mark the request a user logged in on, as `user_logged_in` reports it."""
    router_clear(request, "login")


def clear_on_logout(*, request: HttpRequest, **kwargs: object) -> None:
    """Mark the request a user logged out on, as `user_logged_out` reports it."""
    router_clear(request, "logout")


def _cycled(request: HttpRequest) -> bool:
    """Whether the session of `request` changed its key during the request.

    A signed-cookie session names its data, so its key moves on every write.
    """
    session = getattr(request, "session", None)
    if session is None or isinstance(session, SignedStore):
        return False
    sent = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
    return sent is not None and getattr(session, "session_key", None) != sent


def clearing(request: HttpRequest) -> bool:
    """Whether the response to `request` clears the router, a cycled session too."""
    if getattr(request, _CLEAR_ATTR, None) is None and _cycled(request):
        router_clear(request, "session")
    return getattr(request, _CLEAR_ATTR, None) is not None


def directives(request: HttpRequest, *, snapshot: bool = True) -> tuple[str, ...]:
    """Return the `X-Next-Router` directives the response to `request` carries."""
    found: list[str] = []
    if clearing(request):
        found.append(CLEAR)
    if not snapshot:
        found.append(NO_SNAPSHOT)
    return tuple(found)


def stamp(
    response: HttpResponseBase, request: HttpRequest, *, snapshot: bool = True
) -> HttpResponseBase:
    """Set `X-Next-Router` on `response` when the router is on and a directive holds."""
    if not router_enabled():
        return response
    found = directives(request, snapshot=snapshot)
    if found:
        response[ROUTER] = ", ".join(found)
    return response


__all__ = [
    "CLEAR",
    "NO_SNAPSHOT",
    "clear_on_login",
    "clear_on_logout",
    "clearing",
    "directives",
    "router_clear",
    "stamp",
]
