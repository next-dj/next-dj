"""Shared loading and lazy management of the settings-driven backend families."""

import logging
from collections.abc import Callable, Iterable, Mapping
from functools import cached_property
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.dispatch import Signal

from next.conf import import_class_cached, next_framework_settings


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
        msg = (
            f"A {root.__name__} entry names its backend by a dotted path "
            f"under BACKEND, got {config!r}."
        )
        raise ImproperlyConfigured(msg)
    klass: type[Any] = import_class_cached(dotted)
    if not (isinstance(klass, type) and issubclass(klass, root)):
        msg = f"Backend {dotted!r} is not a {root.__name__} subclass."
        raise ImproperlyConfigured(msg)
    return klass


def _instantiate_backend[T](klass: type[T], config: Mapping[str, Any]) -> T:
    """Build one backend from its config entry.

    Every backend family takes the whole entry as its single argument, a
    contract `type[T]` cannot express, so the class is called through a
    factory signature.
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

    An entry is misconfigured when its dotted path does not resolve into
    the family, or when the backend itself answers `ImproperlyConfigured`.
    Such an entry costs its own backend and nothing else, so a site keeps
    serving with the rest. Anything else a constructor raises is a bug in
    that backend and reaches the caller.
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
            instance = _instantiate_backend(klass, config)
        except ImproperlyConfigured:
            logger.exception("error creating %s from config %s", name, config)
            continue
        if signal is not None:
            signal.send(sender=klass, config=dict(config), instance=instance)
        backends.append(instance)
    return backends


class SingleBackendManager[T]:
    """Instantiates the single backend named by one framework settings key.

    A misconfigured entry raises out of `get()` rather than being logged
    and skipped, because a family with one backend has nothing to fall
    back to.
    """

    def __init__(
        self, setting: str, *, base: BackendRoot[T], default: str | None = None
    ) -> None:
        """Bind the manager to a settings key without reading it."""
        self._setting = setting
        self._base = base
        self._default = default

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
            msg = (
                f"NEXT_FRAMEWORK[{self._setting!r}] names a backend that "
                f"cannot be imported: {exc}"
            )
            raise ImproperlyConfigured(msg) from exc
        return _instantiate_backend(klass, config)

    def get(self) -> T:
        """Return the configured backend, building it on first use."""
        return self._backend

    def reset(self) -> None:
        """Drop the cached backend so the next `get()` rereads settings.

        Invalidation only. The rebuild waits for a caller that asks for
        the backend, so a settings reload nobody follows up on costs
        nothing.
        """
        self.__dict__.pop("_backend", None)


__all__ = [
    "BackendRoot",
    "SingleBackendManager",
    "backend_entries",
    "load_backends",
    "resolve_backend_class",
]
