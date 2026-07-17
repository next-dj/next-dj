"""Router manager, lazy urlpatterns sequence, and settings-reload wiring.

`RouterManager` owns the list of active `RouterBackend` instances and
rebuilds it from `NEXT_FRAMEWORK["PAGE_BACKENDS"]` whenever framework
settings change. `_LazyUrlPatterns` is the sequence wrapped by the
module-level resolver built from `NEXT_FRAMEWORK["URL_RESOLVER"]` so the
first resolve triggers router and form-action resolution without walking
the page tree at import time.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, overload, override

from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver, clear_url_caches
from django.urls.resolvers import RoutePattern

from next.conf import next_framework_settings
from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded
from next.forms.manager import form_action_manager

from .backends import RouterBackend, RouterFactory
from .resolver import TrieURLResolver
from .signals import router_reloaded


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


logger = logging.getLogger(__name__)


class RouterManager:
    """Load `RouterBackend` instances from `NEXT_FRAMEWORK` and iterate them."""

    _version: int = 0
    """Cache token for the lazy urlpatterns concat, bumped by `reload()`."""

    def __init__(self) -> None:
        """Empty backend list until first iteration."""
        self._backends: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    @override
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
        """Rebuild backends from `PAGE_BACKENDS` and notify listeners.

        The Django URL resolver caches resolved patterns. The cache is
        cleared here so the next request sees the freshly built backend
        list. The `router_reloaded` signal fires after the rebuild and
        the cache flush so receivers observe a consistent state.
        """
        self._version += 1
        self._config_cache = None
        self._backends.clear()

        configs = self._get_next_pages_config()
        for config in configs:
            try:
                self._backends.append(RouterFactory.create_backend(config))
            except (ValueError, TypeError, KeyError, ImportError):
                logger.exception("error creating router from config %s", config)

        clear_url_caches()
        router_reloaded.send(sender=type(self))

    def _get_next_pages_config(self) -> list[dict[str, Any]]:
        """Router list from `settings.NEXT_FRAMEWORK` (merged defaults, cached)."""
        if self._config_cache is not None:
            return self._config_cache
        routers = next_framework_settings.PAGE_BACKENDS
        if not isinstance(routers, list):
            self._config_cache = []
            return self._config_cache
        self._config_cache = routers
        return self._config_cache


router_manager = RouterManager()


def _on_settings_reloaded(**_kwargs: object) -> None:
    """Rebuild router backends and the URL resolver on settings reload.

    The resolver is swapped in place so `urlpatterns` keeps its identity
    for the outer include resolver, which iterates the list on every
    resolve and picks up the replacement.
    """
    router_manager.reload()
    urlpatterns[0] = _build_url_resolver()


settings_reloaded.connect(_on_settings_reloaded)


class _LazyUrlPatterns(Sequence["URLPattern | URLResolver"]):
    """Defer expanding router and form patterns until first use.

    Not a `list` subclass, so `include()` defers materialisation to the
    first resolve. Explicit `__reversed__` keeps the resolver's reverse
    walk to one list build instead of one per index. The concat is cached
    against the router and form-action manager versions.
    """

    def __init__(self) -> None:
        """Empty cache until the first pattern build."""
        self._cache: tuple[int, int, list[URLPattern | URLResolver]] | None = None

    def version_token(self) -> tuple[int, int]:
        """Router and form-action versions keying caches derived from this."""
        return (router_manager._version, form_action_manager._version)

    def _patterns(self) -> list[URLPattern | URLResolver]:
        cache = self._cache
        if cache is not None and (cache[0], cache[1]) == (
            router_manager._version,
            form_action_manager._version,
        ):
            return cache[2]
        patterns: list[URLPattern | URLResolver] = [
            *router_manager,
            *form_action_manager,
        ]
        # Versions are read after the build because expanding pages can
        # register form actions and bump the forms version mid-build.
        self._cache = (
            router_manager._version,
            form_action_manager._version,
            patterns,
        )
        return patterns

    @override
    def __iter__(self) -> Iterator[URLPattern | URLResolver]:
        return iter(self._patterns())

    @override
    def __reversed__(self) -> Iterator[URLPattern | URLResolver]:
        return reversed(self._patterns())

    @override
    def __len__(self) -> int:
        return len(self._patterns())

    @overload
    def __getitem__(self, key: int, /) -> URLPattern | URLResolver: ...

    @overload
    def __getitem__(self, key: slice, /) -> list[URLPattern | URLResolver]: ...

    @override
    def __getitem__(
        self, key: int | slice, /
    ) -> URLPattern | URLResolver | list[URLPattern | URLResolver]:
        return self._patterns()[key]


_DEFAULT_URL_RESOLVER = "next.urls.TrieURLResolver"


def _build_url_resolver() -> URLResolver:
    """Instantiate the resolver class named by `NEXT_FRAMEWORK["URL_RESOLVER"]`."""
    dotted = next_framework_settings.URL_RESOLVER
    if dotted == _DEFAULT_URL_RESOLVER:
        # The package binds `TrieURLResolver` only after importing this
        # module, so the import helper would hit a partially initialised
        # `next.urls` when the default is built during package import.
        cls: type[Any] = TrieURLResolver
    else:
        try:
            cls = import_class_cached(dotted)
        except ImportError as exc:
            msg = (
                f"NEXT_FRAMEWORK['URL_RESOLVER'] {dotted!r} could not be "
                f"imported: {exc}"
            )
            raise ImproperlyConfigured(msg) from exc
    if not isinstance(cls, type) or not issubclass(cls, URLResolver):
        msg = (
            f"NEXT_FRAMEWORK['URL_RESOLVER'] {dotted!r} is not a "
            "django.urls.resolvers.URLResolver subclass."
        )
        raise ImproperlyConfigured(msg)
    return cls(RoutePattern(""), _LazyUrlPatterns())


app_name = "next"
urlpatterns = [_build_url_resolver()]


__all__ = [
    "RouterManager",
    "app_name",
    "router_manager",
    "urlpatterns",
]
