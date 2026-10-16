"""Router manager, lazy urlpatterns sequence, and settings-reload wiring.

`_LazyResolverSlot` holds the resolver back until first read, so the first resolve wires
routers and form actions without touching the page tree at import time.
"""

from __future__ import annotations

import itertools
import threading
from typing import TYPE_CHECKING, Any, override

from django.urls import URLPattern, URLResolver, clear_url_caches, path
from django.urls.resolvers import RoutePattern

from next.backends import backend_entries, load_backends, resolve_setting_class
from next.csrf import CSRF_URL_NAME, csrf_view
from next.forms.manager import form_action_manager
from next.pages.manager.views import fit_page_caches
from next.ports import seo_routes_slot

from .backends import RouterBackend
from .resolver import TrieURLResolver
from .signals import router_backend_loaded, router_reloaded
from .urlconf import MountedPatterns


if TYPE_CHECKING:
    from collections.abc import Generator, Sequence


_version_counter = itertools.count(1)
"""Process-wide source of router versions, so no two managers share one."""

type VersionToken = tuple[int, int, int]


class SeoRoutesVersion:
    """The version of the SEO routes `seo_routes_slot` appends after the page routes.

    The value is a plain attribute, so reading the version token makes no port call.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        """Start at zero, the version of routes no area has published yet."""
        self.value = 0

    def move(self) -> None:
        """Advance to a version that no earlier state of these routes carried."""
        self.value = next(_version_counter)


seo_routes_version = SeoRoutesVersion()
"""Moved when the SEO routes slot binds and on every reset of the SEO sources."""


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

        It reports the raw state instead of loading, so a debugger displaying the
        manager neither rebuilds routes nor sends signals.
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


def reload_router_settings(**kwargs) -> None:
    """Rebuild router backends and drop the built URL resolver on settings reload.

    The slot keeps its identity so the outer include resolver, which iterates it on
    every resolve, picks the replacement up on its next read.
    """
    router_manager.reload()
    urlpatterns.reset()


_ROUTED_SETTINGS = frozenset({"INSTALLED_APPS", "ROOT_URLCONF"})


def reload_app_routers(*, setting: str, **kwargs) -> None:
    """Rebuild the routers when the apps or the URLconf behind their page trees move.

    The rebuild bumps the version behind the lazy patterns and clears the URL caches.
    """
    if setting in _ROUTED_SETTINGS:
        router_manager.reload()


class _RoutedPages:
    """The page patterns each URL build of one router version mounts, sized together.

    `next.urls` and each `pages()` tree build apart, and the caches must hold them all.
    """

    def __init__(self) -> None:
        """Start with no build on record."""
        self._version = 0
        self._builds: dict[object, list[URLPattern | URLResolver]] = {}
        self._lock = threading.Lock()

    def fit(self, build: object, patterns: list[URLPattern | URLResolver]) -> None:
        """Record the patterns of one build and size the page caches by every build."""
        with self._lock:
            version = router_manager.version
            if version != self._version:
                self._version = version
                self._builds = {}
            self._builds[build] = patterns
            fit_page_caches(itertools.chain.from_iterable(self._builds.values()))


routed_pages = _RoutedPages()


_CSRF_PATTERN = path("_next/csrf/", csrf_view, name=CSRF_URL_NAME)
"""The endpoint the runtime fetches a CSRF token from when the page HTML omits it."""


class _LazyUrlPatterns(MountedPatterns):
    """Defer expanding CSRF, form, router and SEO patterns until first use.

    Skips `list` so `include()` defers materialisation, overrides `__reversed__` to
    avoid a per-index list build, and caches the concat against its version token.
    """

    def __init__(self) -> None:
        """Empty cache until the first pattern build."""
        self._cache: tuple[VersionToken, list[URLPattern | URLResolver]] | None = None

    def version_token(self) -> VersionToken:
        """Return the router, form-action and SEO route versions keying caches."""
        return (
            router_manager.version,
            form_action_manager.version,
            seo_routes_version.value,
        )

    @override
    def _patterns(self) -> list[URLPattern | URLResolver]:
        cache = self._cache
        if cache is not None and cache[0] == self.version_token():
            return cache[1]
        seo_routes = seo_routes_slot.peek()
        # Pages expand first because loading a page module registers its form actions.
        # The framework routes still lead, so a root catch-all cannot match them.
        pages = list(router_manager)
        routed_pages.fit(app_name, pages)
        patterns: list[URLPattern | URLResolver] = [
            _CSRF_PATTERN,
            *form_action_manager,
            *pages,
            *(() if seo_routes is None else seo_routes.patterns()),
        ]
        if seo_routes is None:
            # Before `ready()` binds the port the list is incomplete and stays uncached.
            return patterns
        # Versions are read after the build because expanding pages can
        # register form actions and bump the forms version mid-build.
        self._cache = (self.version_token(), patterns)
        return patterns


def build_url_resolver(
    patterns: Sequence[URLPattern | URLResolver] | None = None,
) -> URLResolver:
    """Instantiate the resolver class named by `NEXT_FRAMEWORK["URL_RESOLVER"]`.

    It wraps the `next.urls` patterns unless a `pages()` tree hands its own.
    """
    cls = resolve_setting_class(
        "URL_RESOLVER",
        base=URLResolver,
        # The package binds `TrieURLResolver` only after importing this module,
        # so the import helper would hit a half-initialised `next.urls`.
        shipped=TrieURLResolver,
        base_path="django.urls.resolvers.URLResolver",
    )
    return cls(RoutePattern(""), _LazyUrlPatterns() if patterns is None else patterns)


class _LazyResolverSlot(MountedPatterns):
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

    @override
    def _patterns(self) -> tuple[URLPattern | URLResolver, ...]:
        # Held as a ready one-tuple rather than rebuilt per read, because
        # every URL resolve iterates this sequence once.
        slot = self._slot
        if not slot:
            slot = self._slot = (build_url_resolver(),)
        return slot

    @override
    def __len__(self) -> int:
        return 1


app_name = "next"
urlpatterns = _LazyResolverSlot()


__all__ = [
    "RouterManager",
    "app_name",
    "routed_pages",
    "router_manager",
    "seo_routes_version",
    "urlpatterns",
]
