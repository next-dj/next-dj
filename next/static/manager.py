"""Coordinate static backends, asset discovery, and the page-root cache.

Backends load lazily, page-tree roots are held until a reload moves them, and
`default_manager` resets on a `NEXT_FRAMEWORK` change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.signals import setting_changed
from django.utils.functional import LazyObject, empty

from next.backends import BackendListManager, backend_entries, load_backends
from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded
from next.pages.watch import get_pages_directories_for_watch

from .backends import MANIFEST_SETTINGS, StaticBackend
from .collector import StaticCollector
from .discovery import AssetDiscovery, PathResolver
from .inject import PlaceholderInjector
from .scripts import NEXT_JS_STATIC_PATH, NextScriptBuilder
from .signals import backend_loaded


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.http import HttpRequest

    from next.components import ComponentInfo

    from .collector import DedupStrategy, JsContextPolicy


_DEFAULT_BACKEND_PATH = "next.static.StaticFilesBackend"


def _rewrites_asset_urls(backend: StaticBackend) -> bool:
    """Report whether the backend replaces the identity `asset_url` hook.

    Settled once per backend load so a pipeline nothing rewrites pays no call.
    Checked on the instance, so a backend composing its rewrite in `__init__` counts.
    """
    return getattr(backend.asset_url, "__func__", None) is not StaticBackend.asset_url


class StaticManager(BackendListManager[StaticBackend]):
    """Coordinate static backends, asset discovery, and the injector they feed.

    Backends load lazily from `NEXT_FRAMEWORK['STATIC_BACKENDS']` on first access, and
    the default one delegates URL resolution to Django staticfiles.
    """

    def __init__(self) -> None:
        """Initialise empty backend and discovery caches, loaded lazily."""
        # The reload always seeds at least one backend, so the flag only
        # gates the lazy first load and the settings-reload invalidation.
        super().__init__()
        self._discovery: AssetDiscovery | None = None
        self._cached_page_roots: tuple[Path, ...] | None = None
        self._script_builder: NextScriptBuilder | None = None
        self._dedup_factory: Callable[[], DedupStrategy] | None = None
        self._js_policy_factory: Callable[[], JsContextPolicy] | None = None
        self._rewrites_urls: bool = False
        self._injector = PlaceholderInjector(self)

    @property
    def backends(self) -> tuple[StaticBackend, ...]:
        """Return the configured backends in consultation order."""
        self._ensure_backends()
        return tuple(self._backends)

    @property
    def default_backend(self) -> StaticBackend:
        """Return the first configured backend used for file registration."""
        self._ensure_backends()
        return self._backends[0]

    @property
    def discovery(self) -> AssetDiscovery:
        """Return the shared asset discovery instance."""
        if self._discovery is None:
            self._discovery = AssetDiscovery(
                self, resolver=PathResolver(self.page_roots)
            )
        return self._discovery

    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Forward page asset discovery to the shared discovery instance."""
        self._ensure_backends()
        self.discovery.discover_page_assets(file_path, collector)

    def discover_component_assets(
        self, info: ComponentInfo, collector: StaticCollector
    ) -> None:
        """Forward component asset discovery to the shared discovery instance."""
        self._ensure_backends()
        self.discovery.discover_component_assets(info, collector)

    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path | None = None,
        request: HttpRequest | None = None,
    ) -> str:
        """Replace every registered placeholder token with rendered tags."""
        return self._injector.inject(
            html, collector, page_path=page_path, request=request
        )

    def asset_url(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return an already-resolved asset URL as the pipeline renders it.

        A full page render and a partial envelope both ask here,
        so an unrewriting backend pays no call.
        """
        self._ensure_backends()
        if not self._rewrites_urls:
            return url
        return self.default_backend.asset_url(url, request=request)

    def script_builder(self) -> NextScriptBuilder:
        """Return the builder holding the runtime URL and the tag templates."""
        if self._script_builder is None:
            url = str(staticfiles_storage.url(NEXT_JS_STATIC_PATH))
            options = next_framework_settings.NEXT_JS_OPTIONS
            if not isinstance(options, dict):  # pragma: no cover
                options = {}
            self._script_builder = NextScriptBuilder.from_options(url, options)
        return self._script_builder

    @override
    def reload(self) -> None:
        """Rebuild the backend list from merged framework settings.

        A failing entry costs only itself, an empty list falls back to staticfiles.
        """
        self._discovery = None
        self._cached_page_roots = None
        self._script_builder = None
        self._dedup_factory = None
        self._js_policy_factory = None
        self._backends = load_backends(
            backend_entries("STATIC_BACKENDS"),
            base=StaticBackend,
            default=_DEFAULT_BACKEND_PATH,
            signal=backend_loaded,
        ) or load_backends(
            [{}],
            base=StaticBackend,
            default=_DEFAULT_BACKEND_PATH,
            signal=backend_loaded,
        )
        self._mark_loaded()
        self._resolve_collector_strategies()

    def _resolve_collector_strategies(self) -> None:
        """Read the pipeline-level facts the first backend settles for a render.

        One render holds one collector, so only the first `STATIC_BACKENDS` entry
        settles the dedup strategy and JS-context policy for the whole pipeline.
        """
        backend = self._backends[0]
        self._rewrites_urls = _rewrites_asset_urls(backend)
        options = dict(backend.config.get("OPTIONS") or {})
        dedup_path = options.get("DEDUP_STRATEGY")
        policy_path = options.get("JS_CONTEXT_POLICY")
        self._dedup_factory = (
            cast("Callable[[], DedupStrategy]", import_class_cached(str(dedup_path)))
            if dedup_path
            else None
        )
        self._js_policy_factory = (
            cast("Callable[[], JsContextPolicy]", import_class_cached(str(policy_path)))
            if policy_path
            else None
        )

    def create_collector(self) -> StaticCollector:
        """Build a new `StaticCollector` wired with configured strategies."""
        self._ensure_backends()
        dedup = self._dedup_factory() if self._dedup_factory is not None else None
        policy = (
            self._js_policy_factory() if self._js_policy_factory is not None else None
        )
        return StaticCollector(dedup=dedup, js_context_policy=policy)

    def forget_backend_urls(self) -> None:
        """Tell every loaded backend the storage behind its URLs was rebuilt.

        Driven from the backend list, so a third-party backend memoising resolved URLs
        is invalidated on the same terms as the bundled one. The script builder is
        dropped too, since it holds a URL read through that storage.
        """
        self._script_builder = None
        for backend in self._backends:
            backend.forget_urls()

    def page_roots(self) -> tuple[Path, ...]:
        """Return absolute page-tree roots from configured page backends.

        Taken already resolved, as the watch layer spells them, so no lookup repeats it.
        """
        if self._cached_page_roots is None:
            self._cached_page_roots = tuple(get_pages_directories_for_watch())
        return self._cached_page_roots

    def forget_page_roots(self) -> None:
        """Read the page trees again, dropping what was derived from the old ones."""
        self._cached_page_roots = None
        self._discovery = None


class DefaultStaticManager(LazyObject):
    """Lazy handle that defers the construction of a static manager.

    The `next.conf` settings hook resets the wrapper on a `NEXT_FRAMEWORK` change.
    """

    def _setup(self) -> None:
        self._wrapped = StaticManager()


default_manager: DefaultStaticManager = DefaultStaticManager()


def get_static_manager() -> StaticManager:
    """Return the live `StaticManager` behind the lazy default handle.

    A caller that patches a method and restores it needs the instance itself, so a
    settings reload swapping the handle midway cannot misdirect the restore.
    """
    if default_manager._wrapped is empty:
        default_manager._setup()
    return default_manager._wrapped


def collect_component_assets(
    info: ComponentInfo, collector: StaticCollector | None
) -> None:
    """Discover a composite component's co-located assets into the collector.

    A simple component owns no co-located assets and a missing collector is no sink, so
    both short-circuit before the lazy default manager is touched.
    """
    if collector is None or info.is_simple:
        return
    default_manager.discover_component_assets(info, collector)


def reset_default_manager() -> None:
    """Drop the wrapped static manager so the next access rebuilds it.

    Hooked into the `settings_reloaded` signal from `next.conf`, so test code changing
    `NEXT_FRAMEWORK` sees a fresh manager on the next access.
    """
    default_manager._wrapped = empty  # type: ignore[assignment]


def forget_manager_page_roots(**kwargs) -> None:
    """Tell the default manager a reload moved what the routers report.

    A manager nothing has built yet reads them fresh anyway, so the lazy
    handle is left alone rather than woken to be invalidated.
    """
    if default_manager._wrapped is not empty:
        default_manager.forget_page_roots()


def forget_manager_backend_urls(**kwargs) -> None:
    """Tell the default manager the staticfiles storage was rebuilt.

    A manager nothing has built yet holds no backend and no memo, so the lazy
    handle is left alone rather than woken to be invalidated.
    """
    if default_manager._wrapped is not empty:
        default_manager.forget_backend_urls()


def _on_settings_reloaded(**kwargs) -> None:
    """Reset the default static manager when framework settings reload."""
    reset_default_manager()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the derived state a Django setting moved out from under.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half, while `APP_DIRS` trees
    move with `INSTALLED_APPS` and a memoised URL answers for a rebuilt storage.
    """
    if setting == "INSTALLED_APPS":
        forget_manager_page_roots()
    elif setting in MANIFEST_SETTINGS:
        forget_manager_backend_urls()


settings_reloaded.connect(_on_settings_reloaded)
setting_changed.connect(_on_setting_changed)
