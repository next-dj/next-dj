"""System checks for realtime broker settings and optional dependencies."""

import importlib.util
import inspect
import math
import sys
from collections.abc import Mapping

from django.apps import apps
from django.conf import settings
from django.contrib.sessions.backends.signed_cookies import (
    SessionStore as SignedCookieSessionStore,
)
from django.core.cache.backends.db import DatabaseCache
from django.core.cache.backends.dummy import DummyCache
from django.core.cache.backends.filebased import FileBasedCache
from django.core.cache.backends.locmem import LocMemCache
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.utils.module_loading import import_string

from next.checks import NEXT
from next.checks.common import errors_for_unknown_keys
from next.conf import import_class_cached
from next.conf.defaults import USER_SETTING

from .backends import BrokerBackend, MemoryBroker
from .redis import RedisBroker


_PREFIX = "NEXT_FRAMEWORK['REALTIME']"
_BROKER_KEYS = frozenset({"BACKEND", "OPTIONS"})
_REALTIME_KEYS = frozenset(
    {"BROKER", "TRANSPORTS", "TICKET_MAX_AGE", "WS_PATH", "HEARTBEAT"}
)
_BROKER_OPTIONS = {
    "next.realtime.backends.MemoryBroker": frozenset({"QUEUE_SIZE"}),
    "next.realtime.redis.RedisBroker": frozenset(
        {"URL", "PREFIX", "HEALTH_CHECK_INTERVAL"}
    ),
    "next.contrib.channels.broker.ChannelsLayerBroker": frozenset(
        {"CHANNEL_LAYER", "CACHE_ALIAS", "PREFIX"}
    ),
}
_REDIS_BACKEND = "next.realtime.RedisBroker"
_CHANNELS_BACKEND = "next.contrib.channels.ChannelsLayerBroker"
_CHANNELS_MODULE = "next.contrib.channels.broker"
_IN_MEMORY_LAYER = "channels.layers.InMemoryChannelLayer"
_LOCMEM_CACHE = "django.core.cache.backends.locmem.LocMemCache"
_NON_ATOMIC_CACHES = (LocMemCache, DummyCache, DatabaseCache, FileBasedCache)
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
                id="next.E184",
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
                id="next.E185",
            )
        )
    errors.extend(_heartbeat_errors(realtime))
    errors.extend(_broker_errors(broker))
    errors.extend(_deployment_warnings(realtime))
    errors.extend(_channels_broker_warnings(broker))
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
            id="next.E187",
        )
    ]


def _transport_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Validate the transport list and its optional Channels dependency."""
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
                id="next.E182",
            )
        ]
    if "ws" in transports and importlib.util.find_spec("channels") is None:
        return [
            Error(
                f"{_PREFIX} enables 'ws' but Django Channels is not installed.",
                hint="Install next.dj[channels].",
                obj=settings,
                id="next.E183",
            )
        ]
    if "ws" in transports and not apps.is_installed("channels"):
        return [
            Error(
                f"{_PREFIX} enables 'ws' but 'channels' is not in INSTALLED_APPS.",
                hint="Add 'channels' to INSTALLED_APPS.",
                obj=settings,
                id="next.E189",
            )
        ]
    return []


def _websocket_enabled(realtime: Mapping[str, object]) -> bool:
    """Mirror the transport resolution without reading the merged settings."""
    transports = realtime.get("TRANSPORTS")
    if isinstance(transports, list):
        return "ws" in transports
    return importlib.util.find_spec("channels") is not None and apps.is_installed(
        "channels"
    )


def _deployment_warnings(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Flag an enabled WebSocket transport without ASGI or server-side sessions."""
    if not _websocket_enabled(realtime):
        return []
    warnings: list[CheckMessage] = []
    if not getattr(settings, "ASGI_APPLICATION", None):
        warnings.append(
            DjangoWarning(
                "WebSocket transport is enabled, but ASGI_APPLICATION is not "
                "configured.",
                hint=(
                    "Wrap the Django ASGI application with "
                    "next.contrib.channels.asgi.application()."
                ),
                obj=settings,
                id="next.W141",
            )
        )
    middleware = getattr(settings, "MIDDLEWARE", ())
    if not any(item.endswith("SessionMiddleware") for item in middleware):
        warnings.append(
            DjangoWarning(
                "WebSocket tickets require SessionMiddleware on the guarded HTTP "
                "route.",
                obj=settings,
                id="next.W142",
            )
        )
    if _signed_cookie_sessions():
        warnings.append(
            DjangoWarning(
                "WebSocket subscriptions need server-side sessions, but "
                "SESSION_ENGINE stores them in signed cookies.",
                hint="Use the db, cache, cached_db, or file session engine.",
                obj=settings,
                id="next.W145",
            )
        )
    return warnings


def _signed_cookie_sessions() -> bool:
    """Whether the session engine keeps no server-side record a socket can recheck."""
    engine = getattr(settings, "SESSION_ENGINE", "")
    try:
        store = import_string(f"{engine}.SessionStore")
    except ImportError:
        return False
    return isinstance(store, type) and issubclass(store, SignedCookieSessionStore)


def _channels_broker_warnings(broker: Mapping[str, object]) -> list[CheckMessage]:
    """Flag process-local Channels layer or counter storage behind the broker."""
    backend_class = _broker_class(broker.get("BACKEND"))
    # A resolved subclass has already imported the optional broker module.
    module = sys.modules.get(_CHANNELS_MODULE)
    base = getattr(module, "ChannelsLayerBroker", None)
    if (
        backend_class is None
        or not isinstance(base, type)
        or not issubclass(backend_class, base)
    ):
        return []
    raw_options = broker.get("OPTIONS", {})
    options = raw_options if isinstance(raw_options, Mapping) else {}
    warnings: list[CheckMessage] = []
    if _in_memory_layer(options):
        warnings.append(
            DjangoWarning(
                "ChannelsLayerBroker uses an in-process channel layer, so messages "
                "will not reach other application processes.",
                hint="Configure a shared Channels layer such as channels_redis.",
                obj=settings,
                id="next.W143",
            )
        )
    if _unshared_counter_cache(options):
        warnings.append(
            DjangoWarning(
                "ChannelsLayerBroker sequence numbers need a cache whose incr() is "
                "atomic across processes.",
                hint="Configure a shared Redis or Memcached cache for CACHE_ALIAS.",
                obj=settings,
                id="next.W144",
            )
        )
    return warnings


def _in_memory_layer(options: Mapping[str, object]) -> bool:
    """Whether the broker's channel layer lives inside one process."""
    channel_layers = getattr(settings, "CHANNEL_LAYERS", {})
    layer_config = (
        channel_layers.get(str(options.get("CHANNEL_LAYER", "default")), {})
        if isinstance(channel_layers, Mapping)
        else {}
    )
    layer_backend = (
        layer_config.get("BACKEND", _IN_MEMORY_LAYER)
        if isinstance(layer_config, Mapping)
        else _IN_MEMORY_LAYER
    )
    return isinstance(layer_backend, str) and layer_backend.endswith(
        "InMemoryChannelLayer"
    )


def _unshared_counter_cache(options: Mapping[str, object]) -> bool:
    """Whether the counter cache lacks an incr() that is atomic across processes."""
    cache_alias = options.get("CACHE_ALIAS", "default")
    caches = getattr(settings, "CACHES", {})
    cache_config = (
        caches.get(cache_alias, {})
        if isinstance(cache_alias, str) and isinstance(caches, Mapping)
        else {}
    )
    cache_backend = (
        cache_config.get("BACKEND", _LOCMEM_CACHE)
        if isinstance(cache_config, Mapping)
        else _LOCMEM_CACHE
    )
    try:
        klass = import_string(cache_backend) if isinstance(cache_backend, str) else None
    except ImportError:
        return False
    return isinstance(klass, type) and issubclass(klass, _NON_ATOMIC_CACHES)


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
                id="next.E186",
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
    errors = errors_for_unknown_keys(
        dict(options), allowed=allowed, prefix=f"{_PREFIX}['BROKER']['OPTIONS']"
    )
    if backend_class is MemoryBroker:
        size = options.get("QUEUE_SIZE", 256)
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            errors.append(
                Error(
                    f"{_PREFIX}['BROKER']['OPTIONS']['QUEUE_SIZE'] must be a "
                    "positive integer.",
                    obj=settings,
                    id="next.E178",
                )
            )
    if backend_class is RedisBroker:
        interval = options.get("HEALTH_CHECK_INTERVAL", 0)
        if (
            not isinstance(interval, int | float)
            or isinstance(interval, bool)
            or not math.isfinite(interval)
            or interval < 0
        ):
            errors.append(
                Error(
                    f"{_PREFIX}['BROKER']['OPTIONS']['HEALTH_CHECK_INTERVAL'] must "
                    "be a non-negative number of seconds.",
                    obj=settings,
                    id="next.E188",
                )
            )
    return errors


def _dependency_errors(
    backend: object, backend_class: type[BrokerBackend] | None
) -> list[CheckMessage]:
    """Report missing optional packages for public and module-qualified paths."""
    errors: list[CheckMessage] = []
    qualified = (
        f"{backend_class.__module__}.{backend_class.__qualname__}"
        if backend_class is not None
        else backend
        if isinstance(backend, str)
        else ""
    )
    if qualified in {_REDIS_BACKEND, "next.realtime.redis.RedisBroker"} and (
        importlib.util.find_spec("redis") is None
    ):
        errors.append(
            Error(
                f"{_PREFIX} selects RedisBroker but the redis package is not "
                "installed.",
                hint="Install next.dj[broker].",
                obj=settings,
                id="next.E179",
            )
        )
    if qualified in {
        _CHANNELS_BACKEND,
        "next.contrib.channels.broker.ChannelsLayerBroker",
    } and (importlib.util.find_spec("channels") is None):
        errors.append(
            Error(
                f"{_PREFIX} selects ChannelsLayerBroker but Channels is not installed.",
                hint="Install next.dj[channels].",
                obj=settings,
                id="next.E181",
            )
        )
    return errors


__all__ = ["check_realtime_settings"]
