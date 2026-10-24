"""System checks for realtime broker settings and optional dependencies."""

import importlib.util
import inspect
import math
from collections.abc import Callable, Iterator, Mapping, Sequence

from django.conf import settings
from django.contrib.sessions.backends.signed_cookies import (
    SessionStore as SignedCookieSessionStore,
)
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils.module_loading import import_string

from next.checks import NEXT
from next.checks.common import errors_for_unknown_keys
from next.conf import import_class_cached
from next.conf.defaults import USER_SETTING
from next.conf.scopes import scope_value

from .backends import BrokerBackend, MemoryBroker
from .redis import RedisBroker
from .views import TicketIntrospectionView


_PREFIX = "NEXT_FRAMEWORK['REALTIME']"
_OPTIONS = f"{_PREFIX}['BROKER']['OPTIONS']"
_BROKER_KEYS = frozenset({"BACKEND", "OPTIONS"})
_REALTIME_KEYS = frozenset(
    {"BROKER", "TRANSPORTS", "TICKET_MAX_AGE", "WS_PATH", "HEARTBEAT", "GATEWAY_TOKEN"}
)
_BROKER_OPTIONS = {
    "next.realtime.backends.MemoryBroker": frozenset({"QUEUE_SIZE"}),
    "next.realtime.redis.RedisBroker": frozenset(
        {"URL", "PREFIX", "HEALTH_CHECK_INTERVAL", "CLIENT"}
    ),
}
_REDIS_BACKENDS = frozenset(
    {"next.realtime.RedisBroker", "next.realtime.redis.RedisBroker"}
)
_MAX_TICKET_AGE = 600
_DEFAULT_TICKET_AGE = 60
_DEFAULT_HEARTBEAT = 25.0


@register(NEXT)
def check_realtime_settings(*args, **kwargs) -> list[CheckMessage]:
    """Report invalid realtime settings, missing dependencies and risky deployments.

    A missing `REALTIME` key still runs the checks against the defaults.
    """
    raw = getattr(settings, USER_SETTING, None)
    realtime = raw.get("REALTIME", {}) if isinstance(raw, dict) else {}
    if realtime is None:
        realtime = {}
    if not isinstance(realtime, Mapping):
        return []
    errors = errors_for_unknown_keys(
        dict(realtime), allowed=_REALTIME_KEYS, prefix=_PREFIX
    )
    broker = realtime.get("BROKER", {})
    if broker is None:
        broker = {}
    if not isinstance(broker, Mapping):
        return [
            *errors,
            Error(
                f"{_PREFIX}['BROKER'] must be a mapping.", obj=settings, id="next.E177"
            ),
        ]
    errors.extend(_transport_errors(realtime))
    age = realtime.get("TICKET_MAX_AGE", _DEFAULT_TICKET_AGE)
    if (
        not isinstance(age, int)
        or isinstance(age, bool)
        or not 1 <= age <= _MAX_TICKET_AGE
    ):
        errors.append(
            Error(
                f"{_PREFIX}['TICKET_MAX_AGE'] must be an integer from 1 to 600.",
                obj=settings,
                id="next.E182",
            )
        )
    ws_path = realtime.get("WS_PATH", "/_next/ws/")
    if (
        not isinstance(ws_path, str)
        or not ws_path.startswith("/")
        or "?" in ws_path
        or "#" in ws_path
    ):
        errors.append(
            Error(
                f"{_PREFIX}['WS_PATH'] must be absolute and have no query or fragment.",
                obj=settings,
                id="next.E183",
            )
        )
    errors.extend(_heartbeat_errors(realtime))
    errors.extend(_broker_errors(broker))
    errors.extend(_deployment_warnings(realtime))
    return errors


def _heartbeat_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require a positive finite heartbeat interval in seconds."""
    heartbeat = realtime.get("HEARTBEAT", _DEFAULT_HEARTBEAT)
    if (
        isinstance(heartbeat, int | float)
        and not isinstance(heartbeat, bool)
        and math.isfinite(heartbeat)
        and heartbeat > 0
    ):
        return []
    return [
        Error(
            f"{_PREFIX}['HEARTBEAT'] must be a positive finite number of seconds.",
            obj=settings,
            id="next.E185",
        )
    ]


def _transport_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Validate the explicit transport list."""
    transports = realtime.get("TRANSPORTS")
    if transports is None:
        return []
    if (
        not isinstance(transports, list)
        or not transports
        or any(
            not isinstance(item, str) or item not in {"sse", "ws"}
            for item in transports
        )
        or len(set(transports)) != len(transports)
    ):
        return [
            Error(
                f"{_PREFIX}['TRANSPORTS'] must be a unique non-empty list containing "
                "only 'sse' and 'ws'.",
                obj=settings,
                id="next.E181",
            )
        ]
    return []


def _deployment_warnings(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Flag an explicit WebSocket transport on a deployment without server sessions."""
    transports = realtime.get("TRANSPORTS")
    if not isinstance(transports, list) or "ws" not in transports:
        return []
    return websocket_deployment_messages()


def websocket_deployment_messages() -> list[CheckMessage]:
    """Return the session problems that break a WebSocket on this deployment.

    `manage.py check` never sees a mounted socket, so the app reports these itself.
    """
    messages: list[CheckMessage] = []
    middleware = getattr(settings, "MIDDLEWARE", ())
    if not any(item.endswith("SessionMiddleware") for item in middleware):
        messages.append(
            DjangoWarning(
                "WebSocket tickets require SessionMiddleware on the guarded HTTP "
                "route.",
                obj=settings,
                id="next.W141",
            )
        )
    if _signed_cookie_sessions():
        messages.append(
            DjangoWarning(
                "WebSocket subscriptions need server-side sessions, but "
                "SESSION_ENGINE stores them in signed cookies.",
                hint="Use the db, cache, cached_db, or file session engine.",
                obj=settings,
                id="next.W142",
            )
        )
    return messages


def _signed_cookie_sessions() -> bool:
    """Whether the session engine keeps no server-side record a socket can recheck."""
    engine = getattr(settings, "SESSION_ENGINE", "")
    try:
        store = import_string(f"{engine}.SessionStore")
    except ImportError:
        return False
    return isinstance(store, type) and issubclass(store, SignedCookieSessionStore)


def _broker_errors(broker: Mapping[str, object]) -> list[CheckMessage]:
    """Validate the selected backend and its options."""
    errors: list[CheckMessage] = []
    errors.extend(
        errors_for_unknown_keys(
            dict(broker), allowed=_BROKER_KEYS, prefix=f"{_PREFIX}['BROKER']"
        )
    )
    backend = broker.get("BACKEND")
    backend_class = _broker_class(backend)
    options = broker.get("OPTIONS")
    if options is not None and not isinstance(options, Mapping):
        errors.append(
            Error(
                f"{_PREFIX}['BROKER']['OPTIONS'] must be a mapping.",
                obj=settings,
                id="next.E180",
            )
        )
    if isinstance(options, Mapping):
        errors.extend(_option_errors(options, backend_class))
    errors.extend(_dependency_errors(backend, backend_class))
    if backend_class is None:
        errors.append(
            Error(
                f"{_PREFIX}['BROKER']['BACKEND'] must name an importable "
                "concrete BrokerBackend subclass.",
                obj=settings,
                id="next.E184",
            )
        )
    return errors


def _broker_class(backend: object) -> type[BrokerBackend] | None:
    """Resolve the class without constructing optional backend resources."""
    if backend is None:
        return MemoryBroker
    try:
        klass = import_class_cached(backend) if isinstance(backend, str) else None
    except ImportError:
        return None
    if (
        isinstance(klass, type)
        and issubclass(klass, BrokerBackend)
        and not inspect.isabstract(klass)
    ):
        return klass
    return None


def _option_errors(
    options: Mapping[str, object], backend_class: type[BrokerBackend] | None
) -> list[CheckMessage]:
    """Check built-in options while leaving custom backend options to their owner."""
    if backend_class is None:
        return []
    qualified = f"{backend_class.__module__}.{backend_class.__qualname__}"
    allowed = _BROKER_OPTIONS.get(qualified)
    if allowed is None:
        return []
    errors = errors_for_unknown_keys(dict(options), allowed=allowed, prefix=_OPTIONS)
    if backend_class is MemoryBroker:
        size = options.get("QUEUE_SIZE", 256)
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            errors.append(
                Error(
                    f"{_OPTIONS}['QUEUE_SIZE'] must be a positive integer.",
                    obj=settings,
                    id="next.E178",
                )
            )
    if backend_class is RedisBroker:
        errors.extend(_redis_option_errors(options))
    return errors


def _redis_option_errors(options: Mapping[str, object]) -> list[CheckMessage]:
    """Check the Redis interval and client keywords the broker validates at startup."""
    errors: list[CheckMessage] = []
    interval = options.get("HEALTH_CHECK_INTERVAL", 0)
    if (
        not isinstance(interval, int | float)
        or isinstance(interval, bool)
        or not math.isfinite(interval)
        or interval < 0
    ):
        errors.append(
            Error(
                f"{_OPTIONS}['HEALTH_CHECK_INTERVAL'] must be a non-negative number "
                "of seconds.",
                obj=settings,
                id="next.E186",
            )
        )
    client = options.get("CLIENT", {})
    if (
        not isinstance(client, Mapping)
        or not all(isinstance(key, str) for key in client)
        or client.get("decode_responses", True) is not True
    ):
        errors.append(
            Error(
                f"{_OPTIONS}['CLIENT'] must be a mapping of keyword names that "
                "keeps decode_responses on.",
                obj=settings,
                id="next.E187",
            )
        )
    return errors


def _dependency_errors(
    backend: object, backend_class: type[BrokerBackend] | None
) -> list[CheckMessage]:
    """Report a missing redis package for the public and module-qualified paths."""
    qualified = (
        f"{backend_class.__module__}.{backend_class.__qualname__}"
        if backend_class is not None
        else backend
        if isinstance(backend, str)
        else ""
    )
    if qualified in _REDIS_BACKENDS and importlib.util.find_spec("redis") is None:
        return [
            Error(
                f"{_PREFIX} selects RedisBroker but the redis package is not "
                "installed.",
                hint="Install next.dj[broker].",
                obj=settings,
                id="next.E179",
            )
        ]
    return []


@register(Tags.urls, NEXT)
def check_ticket_introspection(*args, **kwargs) -> list[CheckMessage]:
    """Refuse a routed ticket introspection endpoint that no bearer token guards."""
    token = scope_value("REALTIME", "GATEWAY_TOKEN")
    if isinstance(token, str) and token:
        return []
    if not getattr(settings, "ROOT_URLCONF", None):
        return []
    try:
        views = list(_routed_views(get_resolver().url_patterns))
    except (ImportError, ImproperlyConfigured):
        return []
    if not any(_default_access(view) for view in views):
        return []
    return [
        Error(
            "The ticket introspection endpoint is routed, but "
            f"{_PREFIX}['GATEWAY_TOKEN'] is not a non-empty string, so it refuses "
            "every gateway.",
            hint=(
                "Set GATEWAY_TOKEN to a long random secret, or override "
                "TicketIntrospectionView.authorize in a subclass."
            ),
            obj=settings,
            id="next.E188",
        )
    ]


def _routed_views(
    patterns: Sequence[URLResolver | URLPattern],
) -> Iterator[Callable[..., object]]:
    """Yield the view of every pattern the URLconf routes, through each include."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routed_views(pattern.url_patterns)
        else:
            yield pattern.callback


def _default_access(view: Callable[..., object]) -> bool:
    """Whether a view is the introspection endpoint behind the stock bearer check."""
    view_class = getattr(view, "view_class", None)
    return (
        isinstance(view_class, type)
        and issubclass(view_class, TicketIntrospectionView)
        and view_class.authorize is TicketIntrospectionView.authorize
        and "authorize" not in getattr(view, "view_initkwargs", {})
    )


__all__ = [
    "check_realtime_settings",
    "check_ticket_introspection",
    "websocket_deployment_messages",
]
