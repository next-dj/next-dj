"""Lazy selection of the configured realtime broker backend."""

import threading
from collections.abc import Mapping

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured

from next.backends import instantiate_backend, resolve_backend_class
from next.conf import next_framework_settings
from next.conf.defaults import DEFAULTS

from .backends import BrokerBackend
from .signals import broker_backend_loaded


_SETTING = "REALTIME"
_DEFAULT_BACKEND = str(DEFAULTS[_SETTING]["BROKER"]["BACKEND"])


class BrokerManager:
    """Build and cache the backend selected by ``REALTIME.BROKER``."""

    def __init__(self) -> None:
        """Create an empty cache without reading settings."""
        self._backend: BrokerBackend | None = None
        self._lock = threading.Lock()

    def get(self) -> BrokerBackend:
        """Return the configured backend, announcing a new one after the lock drops."""
        backend = self._backend
        if backend is not None:
            return backend
        with self._lock:
            backend = self._backend
            config = None
            if backend is None:
                backend, config = self._build()
                self._backend = backend
        if config is not None:
            broker_backend_loaded.send(
                sender=type(backend), config=config, instance=backend
            )
        return backend

    async def aget(self) -> BrokerBackend:
        """Build outside the event loop so sync and async signal receivers work."""
        backend = self._backend
        if backend is not None:
            return backend
        return await sync_to_async(self.get, thread_sensitive=True)()

    def _build(self) -> tuple[BrokerBackend, dict[str, object]]:
        """Resolve the nested broker entry and construct the selected backend."""
        realtime = getattr(next_framework_settings, _SETTING, {})
        config = realtime.get("BROKER", {}) if isinstance(realtime, Mapping) else {}
        if not isinstance(config, Mapping):
            msg = "NEXT_FRAMEWORK['REALTIME']['BROKER'] must be a mapping."
            raise ImproperlyConfigured(msg)
        try:
            klass = resolve_backend_class(
                config, base=BrokerBackend, default=_DEFAULT_BACKEND
            )
        except ImportError as exc:
            msg = f"Could not import the configured realtime broker: {exc}"
            raise ImproperlyConfigured(msg) from exc
        instance = instantiate_backend(klass, config)
        return instance, dict(config)

    def reset(self) -> None:
        """Drop the cached backend after settings change and close its resources."""
        with self._lock:
            backend, self._backend = self._backend, None
        if backend is not None:
            backend.close()


broker_manager = BrokerManager()


def forget_realtime_broker(**kwargs: object) -> None:
    """Reset the manager when the framework settings reload."""
    broker_manager.reset()


__all__ = ["BrokerManager", "broker_manager", "forget_realtime_broker"]
