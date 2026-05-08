"""Coordinate static backends, asset discovery, and placeholder injection.

The static manager loads backends lazily on first use, owns the shared
asset discovery instance, caches page-tree roots, and replaces every
registered placeholder token with the rendered tags once rendering
completes. It also injects the `next.min.js` wiring unless the
injection policy is `DISABLED`.

The module-level `default_manager` is a lazy handle around a single
static manager instance. Test code may replace the wrapped instance by
assigning to `default_manager._wrapped` without mucking with
module-level state. The settings-change hook in `next.conf` resets the
wrapper when `NEXT_FRAMEWORK` changes.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, cast

from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils.functional import LazyObject, empty

from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded
from next.pages.watch import get_pages_directories_for_watch

from .assets import default_kinds
from .backends import StaticBackend, StaticFilesBackend, StaticsFactory
from .collector import HEAD_CLOSE, StaticCollector, default_placeholders
from .discovery import AssetDiscovery, PathResolver
from .scripts import NEXT_JS_STATIC_PATH, NextScriptBuilder, ScriptInjectionPolicy
from .signals import collector_finalized, html_injected


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.http import HttpRequest

    from next.components import ComponentInfo

    from .assets import StaticAsset
    from .collector import DedupStrategy, JsContextPolicy, PlaceholderSlot
    from .scripts import NextScriptBuilder as NextScriptBuilderType


logger = logging.getLogger(__name__)


_RUNTIME_SLOT_NAME = "scripts"


class StaticManager:
    """Coordinate static backends, asset discovery, and placeholder injection.

    Backends are loaded lazily from
    `NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']` on first access. URL
    resolution is handled by the built-in staticfiles backend by
    default, which delegates to Django staticfiles.
    """

    def __init__(self) -> None:
        """Initialise empty backend and discovery caches, loaded lazily."""
        self._backends: list[StaticBackend] = []
        self._discovery: AssetDiscovery | None = None
        self._cached_page_roots: tuple[Path, ...] | None = None
        self._script_builder: NextScriptBuilderType | None = None
        self._dedup_factory: Callable[[], DedupStrategy] | None = None
        self._js_policy_factory: Callable[[], JsContextPolicy] | None = None

    def __len__(self) -> int:
        """Return the number of configured backends, loading them if needed."""
        self._ensure_backends()
        return len(self._backends)

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
                self,
                resolver=PathResolver(self.page_roots),
            )
        return self._discovery

    def discover_page_assets(
        self,
        file_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Forward page asset discovery to the shared discovery instance."""
        self._ensure_backends()
        self.discovery.discover_page_assets(file_path, collector)

    def discover_component_assets(
        self,
        info: ComponentInfo,
        collector: StaticCollector,
    ) -> None:
        """Forward component asset discovery to the shared discovery instance."""
        self._ensure_backends()  # pragma: no cover
        self.discovery.discover_component_assets(info, collector)  # pragma: no cover

    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path | None = None,
        request: HttpRequest | None = None,
    ) -> str:
        """Replace every registered placeholder token with rendered tags.

        Each slot in `default_placeholders` contributes its bucket of
        collected assets. Asset rendering dispatches through the
        backend method named by `KindRegistry.renderer(asset.kind)`,
        so adding new kinds with new renderer methods does not require
        any changes here. The `scripts` slot also receives the next-dj
        runtime wiring when the injection policy is `AUTO`.

        A missing placeholder is left unchanged because `str.replace`
        returns the original string when there is nothing to replace.
        An empty collector yields empty tag sections. The preload hint
        is injected before `</head>` under the same policy.

        The optional `request` argument is forwarded to backend tag
        renderers and to the `collector_finalized` and `html_injected`
        signals. Backends may use it to rewrite asset URLs based on
        per-request state. The default backend ignores it.
        """
        collector_finalized.send(sender=collector, page_path=page_path, request=request)
        html_before = html
        replaced: tuple[str, ...] | None = None
        if html_injected.receivers:
            replaced = tuple(
                slot.name for slot in default_placeholders if slot.token in html
            )
        backend = self.default_backend
        for slot in default_placeholders:
            rendered = self._render_slot(slot, collector, backend, request=request)
            html = html.replace(slot.token, rendered)
        html = self._inject_preload_hint(html)
        if replaced is not None:
            html_injected.send(
                sender=self,
                html_before=html_before,
                html_after=html,
                collector=collector,
                placeholders_replaced=replaced,
                injected_bytes=len(html) - len(html_before),
                request=request,
            )
        return html

    def _next_script_builder(self) -> NextScriptBuilderType:
        if self._script_builder is None:
            url = str(staticfiles_storage.url(NEXT_JS_STATIC_PATH))
            options = next_framework_settings.NEXT_JS_OPTIONS
            if not isinstance(options, dict):  # pragma: no cover
                options = {}
            self._script_builder = NextScriptBuilder.from_options(url, options)
        return self._script_builder

    def _render_slot(
        self,
        slot: PlaceholderSlot,
        collector: StaticCollector,
        backend: StaticBackend,
        *,
        request: HttpRequest | None,
    ) -> str:
        user_tags = self._render_tags(
            collector.assets_in_slot(slot.name), backend, request=request
        )
        if slot.name == _RUNTIME_SLOT_NAME:
            return self._wrap_with_runtime(user_tags, collector)
        return user_tags

    def _wrap_with_runtime(self, user_tags: str, collector: StaticCollector) -> str:
        builder = self._next_script_builder()
        if builder.policy is ScriptInjectionPolicy.AUTO:
            init_payload = builder.init_script(
                collector.js_context(),
                key_serializers=collector.js_context_serializers(),
            )
            next_scripts = f"{builder.script_tag()}\n{init_payload}\n"
            return next_scripts + user_tags if user_tags else next_scripts
        return user_tags

    def _inject_preload_hint(self, html: str) -> str:
        builder = self._next_script_builder()
        if builder.policy is not ScriptInjectionPolicy.AUTO:
            return html
        replacement = f"{builder.preload_link()}\n{HEAD_CLOSE}"
        return html.replace(HEAD_CLOSE, replacement, 1)

    def _render_tags(
        self,
        assets: list[StaticAsset],
        backend: StaticBackend,
        *,
        request: HttpRequest | None,
    ) -> str:
        return "\n".join(self._render_one(asset, backend, request) for asset in assets)

    def _render_one(
        self,
        asset: StaticAsset,
        backend: StaticBackend,
        request: HttpRequest | None,
    ) -> str:
        if asset.inline is not None:
            return asset.inline
        renderer_name = default_kinds.renderer(asset.kind)
        renderer = getattr(backend, renderer_name)
        return cast("str", renderer(asset.url, request=request))

    def _ensure_backends(self) -> None:
        if not self._backends:
            self._reload_config()

    def _reload_config(self) -> None:
        """Rebuild the backend list from merged framework settings.

        Only `ImportError`, `TypeError`, and `ValueError` from a single
        backend entry are swallowed. Other exceptions propagate so bugs
        in user backends surface loudly.
        """
        self._backends.clear()
        self._discovery = None
        self._cached_page_roots = None
        self._script_builder = None
        self._dedup_factory = None
        self._js_policy_factory = None
        configs = next_framework_settings.DEFAULT_STATIC_BACKENDS
        if not isinstance(configs, list):  # pragma: no cover
            configs = []
        for config in configs:
            if not isinstance(config, dict):  # pragma: no cover
                continue
            try:
                backend = StaticsFactory.create_backend(config)
            except (ImportError, TypeError, ValueError):
                logger.exception("Error creating static backend from config %s", config)
                continue
            self._backends.append(backend)
        if not self._backends:
            self._backends.append(StaticFilesBackend())
        self._resolve_collector_strategies()

    def _resolve_collector_strategies(self) -> None:
        """Read dedup and js-context policy dotted paths from the first backend."""
        options = dict(self.default_backend.config.get("OPTIONS") or {})
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

    def page_roots(self) -> tuple[Path, ...]:
        """Return absolute page-tree roots from configured page backends."""
        if self._cached_page_roots is not None:
            return self._cached_page_roots
        roots: list[Path] = []
        for root in get_pages_directories_for_watch():
            with contextlib.suppress(OSError):
                roots.append(root.resolve())
        self._cached_page_roots = tuple(roots)
        return self._cached_page_roots


class DefaultStaticManager(LazyObject):
    """Lazy handle that defers the construction of a static manager.

    The wrapped manager is built on first access. Tests may replace it
    by assigning to `_wrapped` directly. The settings-change hook in
    `next.conf` resets the wrapper when `NEXT_FRAMEWORK` changes.
    """

    def _setup(self) -> None:
        self._wrapped = StaticManager()


default_manager: DefaultStaticManager = DefaultStaticManager()


def reset_default_manager() -> None:
    """Drop the wrapped static manager so the next access rebuilds it.

    Hooked into the `settings_reloaded` signal from `next.conf` so that
    test code changing `NEXT_FRAMEWORK` via `override_settings` sees a
    fresh manager on the next access.
    """
    default_manager._wrapped = empty  # type: ignore[assignment]


def _on_settings_reloaded(**_kwargs: object) -> None:
    """Reset the default static manager when framework settings reload."""
    reset_default_manager()


settings_reloaded.connect(_on_settings_reloaded)
