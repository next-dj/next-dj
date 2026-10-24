"""ASGI helpers that add authenticated WebSocket routing to a Django app."""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import (  # type: ignore[import-untyped]
    ProtocolTypeRouter,
    URLRouter,
)
from channels.security.websocket import OriginValidator  # type: ignore[import-untyped]
from django.conf import settings
from django.urls import URLPattern, path

from next.realtime.tickets import websocket_path

from .consumers import RealtimeConsumer


if TYPE_CHECKING:
    from collections.abc import Sequence

    from asgiref.typing import ASGIApplication


def application(
    http_application: ASGIApplication,
    *,
    origins: Sequence[str] | None = None,
    route: str | None = None,
) -> ProtocolTypeRouter:
    """Wrap the project's HTTP ASGI app with a session-authenticated WS endpoint.

    The Origin allowlist defaults to `ALLOWED_HOSTS`, which must not contain `*`.
    """
    allowed_origins = tuple(origins) if origins is not None else _origins_from_hosts()
    if not allowed_origins:
        msg = "Configure at least one exact WebSocket Origin."
        raise ValueError(msg)
    websocket_app = URLRouter(websocket_urlpatterns(route))
    protected = OriginValidator(AuthMiddlewareStack(websocket_app), allowed_origins)
    return ProtocolTypeRouter({"http": http_application, "websocket": protected})


def websocket_urlpatterns(route: str | None = None) -> list[URLPattern]:
    """Return the authenticated consumer route for a custom Channels router."""
    route_path = websocket_path() if route is None else route
    if not route_path:
        msg = "The WebSocket route must not be empty."
        raise ValueError(msg)
    return [path(route_path.lstrip("/"), RealtimeConsumer.as_asgi())]


def _origins_from_hosts() -> tuple[str, ...]:
    """Turn `ALLOWED_HOSTS` into scheme-less patterns that match any port.

    A leading dot keeps its Django meaning of the domain and every subdomain.
    """
    hosts = settings.ALLOWED_HOSTS
    if not hosts or "*" in hosts:
        msg = (
            "WebSocket Origin validation needs concrete ALLOWED_HOSTS or an explicit "
            "origins list."
        )
        raise ValueError(msg)
    for host in hosts:
        if not host.lstrip(".") or "/" in host or "*" in host:
            msg = "ALLOWED_HOSTS entries must be host names for WebSockets."
            raise ValueError(msg)
    return tuple(dict.fromkeys(host.lower() for host in hosts))


__all__ = ["application", "websocket_urlpatterns"]
