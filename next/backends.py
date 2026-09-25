"""Shared loading and lazy management of the settings-driven backend families."""

import inspect
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from functools import cached_property
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.dispatch import Signal

from next.conf import import_class_cached, next_framework_settings
from next.conf.defaults import DEFAULTS
from next.errors import (
    AbstractBackendError,
    BackendImportError,
    BackendNotSubclassError,
    BackendPathError,
    SettingImportError,
    SettingNotSubclassError,
)


logger = logging.getLogger(__name__)

# A family root is a class, but an abstract one cannot pass as `type[T]`, so
# it travels under its constructor signature and `_root_class` narrows it back.
type BackendRoot[T] = Callable[..., T]


def _root_class[T](base: BackendRoot[T]) -> type[T]:
    """Return the family root as the class every family declares it to be."""
    return cast("type[T]", base)


def resolve_backend_class[T](
    config: Mapping[str, Any], *, base: BackendRoot[T], default: str | None = None
) -> type[T]:
    """Return the class named by `config['BACKEND']`, checked against `base`."""
    root = _root_class(base)
    dotted = config.get("BACKEND", default)
    if not isinstance(dotted, str) or not dotted:
        raise BackendPathError(root.__name__, config)
    klass: type[Any] = import_class_cached(dotted)
    if not (isinstance(klass, type) and issubclass(klass, root)):
        raise BackendNotSubclassError(dotted, root.__name__)
    if inspect.isabstract(klass):
        # The abstract family roots are what a settings entry most likely names
        # by mistake, and instantiating one answers a TypeError no caller reads.
        raise AbstractBackendError(dotted)
    return klass


def _setting_value(setting: str, scope: str | None) -> tuple[object, str]:
    """Return the value one dotted-path key holds and the default it falls back to."""
    if scope is None:
        return getattr(next_framework_settings, setting), DEFAULTS[setting]
    default: str = DEFAULTS[scope][setting]
    return getattr(next_framework_settings, scope).get(setting, default), default


def resolve_setting_class[T](
    setting: str,
    *,
    base: BackendRoot[T],
    shipped: BackendRoot[T],
    base_path: str,
    scope: str | None = None,
) -> type[T]:
    """Return the class named by one dotted-path setting, checked against `base`.

    The shipped default skips the import, since its package is still importing then.
    """
    root = _root_class(base)
    dotted, default = _setting_value(setting, scope)
    if dotted == default:
        klass: object = shipped
    elif not isinstance(dotted, str):
        raise SettingNotSubclassError(setting, dotted, base_path, scope=scope)
    else:
        try:
            klass = import_class_cached(dotted)
        except ImportError as exc:
            raise SettingImportError(setting, dotted, exc, scope=scope) from exc
    if not isinstance(klass, type) or not issubclass(klass, root):
        raise SettingNotSubclassError(setting, dotted, base_path, scope=scope)
    if inspect.isabstract(klass):
        raise AbstractBackendError(str(dotted))
    return klass


def instantiate_backend[T](klass: type[T], config: Mapping[str, Any]) -> T:
    """Build one backend from its config entry.

    Every backend family takes the whole entry as its single argument, a contract
    `type[T]` cannot express, so the class is called through a factory signature.
    """
    factory = cast("Callable[[Mapping[str, Any]], T]", klass)
    return factory(config)


def backend_entries(setting: str) -> list[dict[str, Any]]:
    """Return the dict entries under one list-valued framework settings key."""
    raw = getattr(next_framework_settings, setting, [])
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def load_backends[T](
    configs: Iterable[Mapping[str, Any]],
    *,
    base: BackendRoot[T],
    default: str | None = None,
    signal: Signal | None = None,
) -> list[T]:
    """Instantiate every configured backend, skipping the misconfigured entries.

    A bad dotted path or an `ImproperlyConfigured` backend costs only its own entry, so
    the site keeps serving. Anything else a constructor raises is a bug and propagates.
    """
    name = _root_class(base).__name__
    backends: list[T] = []
    for config in configs:
        try:
            klass = resolve_backend_class(config, base=base, default=default)
        except (ImproperlyConfigured, ImportError):
            logger.exception("error resolving %s from config %s", name, config)
            continue
        try:
            instance = instantiate_backend(klass, config)
        except ImproperlyConfigured:
            logger.exception("error creating %s from config %s", name, config)
            continue
        if signal is not None:
            signal.send(sender=klass, config=dict(config), instance=instance)
        backends.append(instance)
    return backends


class BackendListManager[T](ABC):
    """Holds a settings-driven backend list, loaded on the first access after a reset.

    `reload` is the subclass's own read of its settings key, and `_mark_loaded` is
    where a family says whether an empty result counts as a load at all.
    """

    def __init__(self, backends: Iterable[T] | None = None) -> None:
        """Take an explicit list, or leave the settings read to the first access."""
        self._backends: list[T] = list(backends) if backends is not None else []
        self._loaded: bool = bool(self._backends)
        self._lock = threading.RLock()

    @abstractmethod
    def reload(self) -> None:
        """Rebuild the backend list from the current `NEXT_FRAMEWORK` settings."""

    def _ensure_backends(self) -> None:
        """Load the backends once, on the first access after a reset.

        A settings reload drops the flag at runtime, so the load is double-checked
        under a lock rather than left as a check-then-act two threads both win.
        """
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:
                self.reload()

    def _mark_loaded(self, *, retry_when_empty: bool = False) -> None:
        """Record the load, leaving the flag down where an empty list is no result.

        A family that reads an empty load as a broken configuration rereads the
        settings on the next access rather than serving nothing for good.
        """
        self._loaded = bool(self._backends) or not retry_when_empty


class SingleBackendManager[T]:
    """Instantiates the single backend named by one framework settings key.

    A misconfigured entry raises out of `get()` rather than being logged and skipped,
    because a family with one backend has nothing to fall back to.
    """

    def __init__(
        self,
        setting: str,
        *,
        base: BackendRoot[T],
        default: str | None = None,
        signal: Signal | None = None,
    ) -> None:
        """Bind the manager to a settings key without reading it."""
        self._setting = setting
        self._base = base
        self._default = default
        self._signal = signal

    def _select_config(self) -> Mapping[str, Any]:
        raw = getattr(next_framework_settings, self._setting, None)
        if isinstance(raw, Mapping):
            return raw
        if isinstance(raw, list):
            return next((entry for entry in raw if isinstance(entry, Mapping)), {})
        return {}

    @cached_property
    def _backend(self) -> T:
        config = self._select_config()
        try:
            klass = resolve_backend_class(
                config, base=self._base, default=self._default
            )
        except ImportError as exc:
            raise BackendImportError(self._setting, exc) from exc
        instance = instantiate_backend(klass, config)
        if self._signal is not None:
            self._signal.send(sender=klass, config=dict(config), instance=instance)
        return instance

    def get(self) -> T:
        """Return the configured backend, building it on first use."""
        return self._backend

    def reset(self) -> None:
        """Drop the cached backend so the next `get()` rereads settings.

        Invalidation only. The rebuild waits for a caller that asks for the backend, so
        a settings reload nobody follows up on costs nothing.
        """
        self.__dict__.pop("_backend", None)


__all__ = [
    "BackendListManager",
    "BackendRoot",
    "SingleBackendManager",
    "backend_entries",
    "instantiate_backend",
    "load_backends",
    "resolve_backend_class",
    "resolve_setting_class",
]
