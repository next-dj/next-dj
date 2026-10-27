"""Lazy selection of the configured realtime broker backend."""

import threading
from collections.abc import Mapping

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured

from next.backends import instantiate_backend, resolve_backend_class
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.scopes import scope_value

from .backends import BrokerBackend
from .signals import broker_backend_loaded


_DEFAULT_BACKEND = str(FROZEN_DEFAULTS["REALTIME"]["BROKER"]["BACKEND"])


def broker_class(config: Mapping[str, object]) -> type[BrokerBackend]:
    """Resolve the concrete class a broker entry selects without building it."""
    try:
        return resolve_backend_class(
            config, base=BrokerBackend, default=_DEFAULT_BACKEND
        )
    except ImportError as exc:
        msg = f"Could not import the configured realtime broker: {exc}"
        raise ImproperlyConfigured(msg) from exc


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
        """Build on the shared pool, so no socket waits on the one sync thread."""
        backend = self._backend
        if backend is not None:
            return backend
        return await sync_to_async(self.get, thread_sensitive=False)()

    def backend_class(self) -> type[BrokerBackend]:
        """Return the class of the configured backend without building one."""
        backend = self._backend
        if backend is not None:
            return type(backend)
        return self._resolve()[0]

    def _build(self) -> tuple[BrokerBackend, dict[str, object]]:
        """Resolve the nested broker entry and construct the selected backend."""
        klass, config = self._resolve()
        instance = instantiate_backend(klass, config)
        return instance, dict(config)

    def _resolve(self) -> tuple[type[BrokerBackend], Mapping[str, object]]:
        """Resolve the class the nested broker entry selects."""
        config = scope_value("REALTIME", "BROKER")
        if not isinstance(config, Mapping):
            msg = "NEXT_FRAMEWORK['REALTIME']['BROKER'] must be a mapping."
            raise ImproperlyConfigured(msg)
        return broker_class(config), config

    def reset(self) -> None:
        """Drop the cached backend after settings change and close its resources."""
        with self._lock:
            backend, self._backend = self._backend, None
        if backend is not None:
            backend.close()

    async def aclose(self) -> None:
        """Drop the cached backend and release its resources from an event loop."""
        with self._lock:
            backend, self._backend = self._backend, None
        if backend is not None:
            await backend.aclose()


broker_manager = BrokerManager()


def forget_realtime_broker(**kwargs: object) -> None:
    """Reset the manager when the framework settings reload."""
    broker_manager.reset()


__all__ = ["BrokerManager", "broker_class", "broker_manager", "forget_realtime_broker"]
