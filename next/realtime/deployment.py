"""Report the deployment settings a mounted WebSocket endpoint cannot work under."""

from collections.abc import Sequence

from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.signed_cookies import (
    SessionStore as SignedCookieSessionStore,
)
from django.core.checks import CheckMessage, Warning as DjangoWarning

from next.conf.scopes import scope_value
from next.utils import middleware_listed

from .binding import (
    SessionBinding,
    SignedCookieBinding,
    TicketBinding,
    binding_class,
    session_store_class,
)


_SESSION_MIDDLEWARE = "django.contrib.sessions.middleware.SessionMiddleware"


def websocket_deployment_messages() -> list[CheckMessage]:
    """Return the session problems that break a WebSocket on this deployment.

    `manage.py check` never sees a mounted socket, so the app reports these itself.
    """
    messages: list[CheckMessage] = []
    sessions = middleware_listed(settings.MIDDLEWARE, _SESSION_MIDDLEWARE)
    if not sessions and _bound_to(SessionBinding):
        messages.append(
            DjangoWarning(
                "WebSocket tickets require SessionMiddleware on the guarded HTTP "
                "route.",
                obj=settings,
                id="next.W141",
            )
        )
    if _signed_cookie_sessions() and _bound_to(SessionBinding):
        messages.append(
            DjangoWarning(
                "WebSocket tickets are bound to the session key, which the "
                "signed-cookie SESSION_ENGINE has no value for on a first visit and "
                "re-signs on every session save, so negotiation answers 503 or the "
                "socket closes with 4401.",
                hint=(
                    "Set REALTIME['TICKET_BINDING'] to "
                    "'next.realtime.SignedCookieBinding', or use a server-side session "
                    "engine. A signed cookie still outlives logout until "
                    "SESSION_COOKIE_AGE."
                ),
                obj=settings,
                id="next.W142",
            )
        )
    if _server_sessions() and _bound_to(SignedCookieBinding):
        messages.append(
            DjangoWarning(
                "WebSocket tickets are bound by SignedCookieBinding, which reloads a "
                "signed-in user by pk and auth hash, so a socket outlives logout and "
                "the expiry of the server-side SESSION_ENGINE.",
                hint=(
                    "Set REALTIME['TICKET_BINDING'] to 'next.realtime.SessionBinding', "
                    "which rechecks the stored session."
                ),
                obj=settings,
                id="next.W147",
            )
        )
    return messages


def tunnel_origin_messages(origins: Sequence[str]) -> list[CheckMessage]:
    """Warn about socket origins CSRF refuses, so their tunneled posts answer 403.

    `WebSocketApp` reports this itself, since `manage.py check` never sees the list.
    """
    if not origins:
        return []
    return [
        DjangoWarning(
            f"WebSocketApp admits the origins {', '.join(map(repr, origins))}, which "
            "neither CSRF_TRUSTED_ORIGINS nor a host ALLOWED_HOSTS names in full "
            "covers, so a form action tunneled from them may fail the CSRF check.",
            hint="Add them to CSRF_TRUSTED_ORIGINS or set REALTIME['TUNNEL'] to False.",
            obj=settings,
            id="next.W146",
        )
    ]


def _bound_to(base: type[TicketBinding]) -> bool:
    """Whether the configured ticket binding is `base` or a subclass of it."""
    klass = binding_class(scope_value("REALTIME", "TICKET_BINDING"))
    return klass is not None and issubclass(klass, base)


def _session_store() -> type[SessionBase] | None:
    """Return the session engine's store class, None when it does not import."""
    try:
        store = session_store_class()
    except (ImportError, AttributeError):
        return None
    return store if isinstance(store, type) and issubclass(store, SessionBase) else None


def _signed_cookie_sessions() -> bool:
    """Whether the session engine keeps no server-side record a socket can recheck."""
    store = _session_store()
    return store is not None and issubclass(store, SignedCookieSessionStore)


def _server_sessions() -> bool:
    """Whether the session engine keeps a server-side record a socket can recheck."""
    store = _session_store()
    return store is not None and not issubclass(store, SignedCookieSessionStore)


__all__ = ["tunnel_origin_messages", "websocket_deployment_messages"]
