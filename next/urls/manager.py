"""Router manager, lazy urlpatterns sequence, and settings-reload wiring.

`_LazyResolverSlot` holds the resolver back until first read, so the first resolve wires
routers and form actions without touching the page tree at import time.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, overload, override

from django.core.signals import setting_changed
from django.urls import URLPattern, URLResolver, clear_url_caches
from django.urls.resolvers import RoutePattern

from next.backends import backend_entries, load_backends, resolve_setting_class
from next.conf.signals import settings_reloaded
from next.forms.manager import form_action_manager
from next.ports import seo_routes_slot

from .backends import RouterBackend
from .resolver import TrieURLResolver
from .signals import router_backend_loaded, router_reloaded


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


_version_counter = itertools.count(1)
"""Process-wide source of router versions, so no two managers share one."""


class RouterManager:
    """Load `RouterBackend` instances from `NEXT_FRAMEWORK` and iterate them."""

    def __init__(self) -> None:
        """Empty backend list until the first load."""
        self._version = next(_version_counter)
        self._backends: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None
        self._loaded: bool = False
        self._building_thread: int | None = None
        self._lock = threading.RLock()

    @property
    def version(self) -> int:
        """Cache token for the lazy urlpatterns concat, moved by `reload()`.

        Read it to key a cache of your own on the active backend list. The token is
        drawn process-wide, so a second manager never reuses the number of the first.
        """
        return self._version

    def _bump(self) -> None:
        self._version = next(_version_counter)

    def _ensure_loaded(self) -> None:
        """Build the backend list on the first read, whichever accessor asks.

        The in-build escape is keyed on the building thread, so a backend consulting the
        manager on construction cannot recurse while another thread waits for the build.
        """
        if self._loaded or self._building_thread == threading.get_ident():
            return
        with self._lock:
            if not self._loaded:
                self.reload()

    @property
    def backends(self) -> tuple[RouterBackend, ...]:
        """Return the loaded backends in routing order, loading on first read.

        The returned tuple is a snapshot detached from the live list.
        """
        self._ensure_loaded()
        return tuple(self._backends)

    @override
    def __repr__(self) -> str:
        """Debug representation with backend count and load state.

        It reports the raw state instead of loading, because a repr that
        rebuilt routes and fired signals would be a trap under a debugger.
        """
        return (
            f"<{self.__class__.__name__} backends={len(self._backends)} "
            f"loaded={self._loaded}>"
        )

    def __iter__(self) -> Generator[URLPattern | URLResolver, None, None]:
        """All patterns from each backend, loading config on first use.

        Iteration yields URL patterns rather than backends, so the manager
        deliberately offers no `len` or indexing. Read `backends` for those.
        """
        self._ensure_loaded()
        for backend in self._backends:
            yield from backend.generate_urls()

    def reload(self, *, notify: bool = True) -> None:
        """Rebuild backends from `PAGE_BACKENDS` and notify listeners.

        The rebuild clears the URL caches, sends `router_backend_loaded` per backend and
        then `router_reloaded`, and `notify=False` skips all three. A reentrant lock
        lets a receiver reload from this thread without deadlocking.
        """
        with self._lock:
            self._bump()
            self._config_cache = None

            # Recorded for the whole build, so a backend reading the manager during
            # construction is answered instead of deadlocking on this thread's lock.
            self._building_thread = threading.get_ident()
            try:
                built = load_backends(
                    self._get_next_pages_config(),
                    base=RouterBackend,
                    signal=router_backend_loaded if notify else None,
                )
            finally:
                self._building_thread = None

            # One-step publish, so a mid-build read never sees a partial list.
            self._backends = built
            # Set before the signal, so a receiver reading `backends` stays out.
            self._loaded = True
            if notify:
                clear_url_caches()
                router_reloaded.send(sender=type(self))

    def _get_next_pages_config(self) -> list[dict[str, Any]]:
        """Router entries from `PAGE_BACKENDS`, read once per load."""
        if self._config_cache is None:
            self._config_cache = backend_entries("PAGE_BACKENDS")
        return self._config_cache


router_manager = RouterManager()


def _on_settings_reloaded(**kwargs) -> None:
    """Rebuild router backends and drop the built URL resolver on settings reload.

    The slot keeps its identity so the outer include resolver, which iterates it on
    every resolve, picks the replacement up on its next read.
    """
    router_manager.reload()
    urlpatterns.reset()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Rebuild the routers when the app list behind their page trees moves.

    The rebuild bumps the version behind the lazy patterns, clears Django's URL caches,
    and drops a file router's pattern cache, which is keyed on the app name alone.
    """
    if setting == "INSTALLED_APPS":
        router_manager.reload()


settings_reloaded.connect(_on_settings_reloaded)
setting_changed.connect(_on_setting_changed)


type _VersionToken = tuple[int, int, int]


class _LazyUrlPatterns(Sequence["URLPattern | URLResolver"]):
    """Defer expanding router, form and SEO patterns until first use.

    Skips `list` so `include()` defers materialisation, overrides `__reversed__` to
    avoid a per-index list build, and caches the concat against every manager version.
    """

    def __init__(self) -> None:
        """Empty cache until the first pattern build.

        The seo version source is bound here, because the token is read on every
        resolve and the slot never rebinds once the app is ready.
        """
        self._cache: tuple[_VersionToken, list[URLPattern | URLResolver]] | None = None
        self._seo = seo_routes_slot.get().version_source()

    def version_token(self) -> _VersionToken:
        """Router, form-action and SEO versions keying caches derived from this."""
        return (router_manager.version, form_action_manager.version, self._seo.version)

    def _patterns(self) -> list[URLPattern | URLResolver]:
        cache = self._cache
        if cache is not None and cache[0] == self.version_token():
            return cache[1]
        patterns: list[URLPattern | URLResolver] = [
            *router_manager,
            *form_action_manager,
            *seo_routes_slot.get().patterns(),
        ]
        # Versions are read after the build because expanding pages can
        # register form actions and bump the forms version mid-build.
        self._cache = (self.version_token(), patterns)
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


def _build_url_resolver() -> URLResolver:
    """Instantiate the resolver class named by `NEXT_FRAMEWORK["URL_RESOLVER"]`."""
    cls = resolve_setting_class(
        "URL_RESOLVER",
        base=URLResolver,
        # The package binds `TrieURLResolver` only after importing this module,
        # so the import helper would hit a half-initialised `next.urls`.
        shipped=TrieURLResolver,
        base_path="django.urls.resolvers.URLResolver",
    )
    return cls(RoutePattern(""), _LazyUrlPatterns())


class _LazyResolverSlot(Sequence["URLPattern | URLResolver"]):
    """Hold the outer resolver in one slot and build it on first read.

    Building at import time would read `NEXT_FRAMEWORK` before Django settings are set,
    which a pytest plugin hits before pytest-django exports the settings module.
    """

    def __init__(self) -> None:
        """Empty slot until the first resolver build."""
        self._slot: tuple[URLPattern | URLResolver, ...] = ()

    def reset(self) -> None:
        """Drop the built resolver so the next read rebuilds it from settings."""
        self._slot = ()

    def _built(self) -> tuple[URLPattern | URLResolver, ...]:
        # Held as a ready one-tuple rather than rebuilt per read, because
        # every URL resolve iterates this sequence once.
        slot = self._slot
        if not slot:
            slot = self._slot = (_build_url_resolver(),)
        return slot

    @override
    def __iter__(self) -> Iterator[URLPattern | URLResolver]:
        return iter(self._built())

    @override
    def __len__(self) -> int:
        return 1

    @overload
    def __getitem__(self, key: int, /) -> URLPattern | URLResolver: ...

    @overload
    def __getitem__(self, key: slice, /) -> list[URLPattern | URLResolver]: ...

    @override
    def __getitem__(
        self, key: int | slice, /
    ) -> URLPattern | URLResolver | list[URLPattern | URLResolver]:
        if isinstance(key, slice):
            return list(self._built()[key])
        return self._built()[key]


app_name = "next"
urlpatterns = _LazyResolverSlot()


__all__ = ["RouterManager", "app_name", "router_manager", "urlpatterns"]
