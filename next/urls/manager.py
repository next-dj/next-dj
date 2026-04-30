"""Router manager, lazy urlpatterns list, and settings-reload wiring.

`RouterManager` owns the list of active `RouterBackend` instances and
rebuilds it from `NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"]` whenever
framework settings change. `_LazyUrlPatterns` is a `list`-subclass
used as Django's `urlpatterns` so the first access triggers router
and form-action resolution without walking the page tree at import
time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, SupportsIndex, overload

from django.urls import clear_url_caches

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.forms import form_action_manager

from .backends import RouterBackend, RouterFactory
from .signals import router_reloaded


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from django.urls import URLPattern, URLResolver


logger = logging.getLogger(__name__)


class RouterManager:
    """Load `RouterBackend` instances from `NEXT_FRAMEWORK` and iterate them."""

    def __init__(self) -> None:
        """Empty backend list until first iteration."""
        self._backends: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:
        """Debug representation with backend count."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __len__(self) -> int:
        """Return the number of configured backends."""
        return len(self._backends)

    def __iter__(self) -> Generator[URLPattern | URLResolver, None, None]:
        """All patterns from each backend, loading config on first use."""
        if not self._backends:
            self.reload()
        for backend in self._backends:
            yield from backend.generate_urls()

    def __getitem__(self, index: int) -> RouterBackend:
        """Return the backend at the given index."""
        return self._backends[index]

    def reload(self) -> None:
        """Rebuild backends from `DEFAULT_PAGE_BACKENDS` and notify listeners.

        The Django URL resolver caches resolved patterns. The cache is
        cleared here so the next request sees the freshly built backend
        list. The `router_reloaded` signal fires after the rebuild and
        the cache flush so receivers observe a consistent state.
        """
        self._config_cache = None
        self._backends.clear()

        configs = self._get_next_pages_config()
        for config in configs:
            try:
                if backend := RouterFactory.create_backend(config):
                    self._backends.append(backend)
            except Exception:
                logger.exception("error creating router from config %s", config)

        clear_url_caches()
        router_reloaded.send(sender=type(self))

    def _get_next_pages_config(self) -> list[dict[str, Any]]:
        """Router list from `settings.NEXT_FRAMEWORK` (merged defaults, cached)."""
        if self._config_cache is not None:
            return self._config_cache
        routers = next_framework_settings.DEFAULT_PAGE_BACKENDS
        if not isinstance(routers, list):
            self._config_cache = []
            return self._config_cache
        self._config_cache = routers
        return self._config_cache


router_manager = RouterManager()


def _on_settings_reloaded(**_kwargs: object) -> None:
    """Rebuild router backends when framework settings reload."""
    router_manager.reload()


settings_reloaded.connect(_on_settings_reloaded)


class _LazyUrlPatterns(list):
    """Defer expanding router and form patterns until first use.

    Avoids walking the tree at import time. Rebuilds from
    `router_manager` and `form_action_manager` on each access.
    Subclasses `list` so `isinstance(urlpatterns, list)` holds.
    Overrides `__reversed__` because the inherited empty internal
    buffer would break `reversed(urlpatterns)` in Django's URL
    resolver.
    """

    def _patterns(self) -> list[URLPattern | URLResolver]:
        return [
            *list(router_manager),
            *list(form_action_manager),
        ]

    def __iter__(self) -> Iterator[URLPattern | URLResolver]:
        return iter(self._patterns())

    def __reversed__(self) -> Iterator[URLPattern | URLResolver]:
        return reversed(self._patterns())

    def __len__(self) -> int:
        return len(self._patterns())

    @overload
    def __getitem__(self, key: SupportsIndex, /) -> URLPattern | URLResolver:
        raise NotImplementedError

    @overload
    def __getitem__(self, key: slice, /) -> list[URLPattern | URLResolver]:
        raise NotImplementedError

    def __getitem__(
        self, key: SupportsIndex | slice, /
    ) -> URLPattern | URLResolver | list[URLPattern | URLResolver]:
        return self._patterns()[key]


app_name = "next"
urlpatterns = _LazyUrlPatterns()


__all__ = [
    "RouterManager",
    "app_name",
    "router_manager",
    "urlpatterns",
]
