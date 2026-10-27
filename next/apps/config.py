"""`AppConfig` for next-dj that wires reloader, templates, finders, and components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

from django.apps import AppConfig
from django.core.signals import setting_changed

from next.checks import register_all as _register_checks
from next.components.manager import forget_component_backends, forget_render_pipeline
from next.components.sources import reset_components_manager_cache
from next.conf.settings import reload_on_setting_changed
from next.conf.signals import settings_reloaded
from next.consent.manager import forget_consent_settings
from next.csrf import forget_csrf_delivery
from next.deps.objects import forget_object_rows
from next.deps.resolver import (
    apply_resolver_setting,
    forget_dep_caches,
    reapply_resolver_setting,
)
from next.diagnostics import reset_failure_logs
from next.discovery import reset_router_manager_cache
from next.forms.backends import forget_action_urls
from next.forms.manager import form_action_manager
from next.forms.ports import ActionOwnersImpl
from next.forms.wizard import forget_wizard_backend
from next.pages.access import forget_page_access
from next.pages.loaders import (
    forget_app_page_roots,
    forget_page_roots,
    forget_registered_loaders,
    forget_shared_layouts,
)
from next.pages.metadata.backends import forget_metadata_renderer
from next.pages.metadata.hreflang import (
    forget_translated_urls,
    forget_urlconf_translations,
)
from next.pages.metadata.resolve import forget_crumb_routes
from next.pages.metadata.scope import forget_metadata_scope
from next.pages.ports import PageScanImpl
from next.pages.processors import forget_context_processors, forget_template_processors
from next.pages.responses import forget_response_policies
from next.pages.watch import forget_watch_state
from next.partial.manager import forget_asset_version, forget_partial_backend
from next.partial.ports import PartialShaperImpl
from next.permissions.access import forget_login_default
from next.ports import (
    action_owners_slot,
    form_routes_slot,
    page_scan_slot,
    page_scripts_slot,
    partial_shaper_slot,
    realtime_negotiation_slot,
    router_access_slot,
    seo_routes_slot,
    static_assets_slot,
)
from next.realtime.asgi import forget_trusted_origins
from next.realtime.manager import forget_realtime_broker
from next.realtime.ports import RealtimeNegotiationImpl
from next.realtime.transports import forget_transports
from next.scripts.manager import forget_script_srcs, forget_scripts
from next.scripts.ports import PageScriptsImpl
from next.seo.manager import forget_seo_sources
from next.seo.ports import SeoRoutesImpl
from next.site.config import forget_site_config
from next.static.finders import forget_scans
from next.static.manager import (
    forget_manager_page_roots,
    forget_static_manager,
    forget_storage_state,
)
from next.static.nonce import forget_nonce_setting
from next.static.ports import StaticAssetsImpl
from next.static.serializers import forget_serializer
from next.urls.checks import reset_collected_patterns_cache
from next.urls.manager import (
    reload_app_routers,
    reload_router_settings,
    seo_routes_version,
)
from next.urls.ports import RouterAccessImpl
from next.urls.signals import router_reloaded

from . import autodiscover, autoreload, components, staticfiles, templates


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.dispatch import Signal


_SETTING_CHANGED: Final[tuple[Callable[..., None], ...]] = (
    reload_on_setting_changed,
    forget_trusted_origins,
    forget_app_page_roots,
    forget_urlconf_translations,
    forget_template_processors,
    forget_render_pipeline,
    forget_action_urls,
    forget_asset_version,
    forget_storage_state,
    reload_app_routers,
    forget_script_srcs,
    forget_login_default,
    staticfiles.reinstall_finders,
    templates.reinstall_builtins,
)

_SETTINGS_RELOADED: Final[tuple[Callable[..., None], ...]] = (
    reset_failure_logs,
    reset_router_manager_cache,
    reapply_resolver_setting,
    forget_watch_state,
    forget_shared_layouts,
    forget_page_roots,
    forget_registered_loaders,
    forget_site_config,
    forget_translated_urls,
    forget_metadata_scope,
    forget_crumb_routes,
    forget_metadata_renderer,
    forget_context_processors,
    forget_csrf_delivery,
    forget_response_policies,
    forget_page_access,
    forget_component_backends,
    reset_components_manager_cache,
    forget_wizard_backend,
    forget_partial_backend,
    forget_serializer,
    forget_scans,
    forget_nonce_setting,
    forget_static_manager,
    reset_collected_patterns_cache,
    reload_router_settings,
    forget_seo_sources,
    forget_scripts,
    forget_consent_settings,
    forget_realtime_broker,
    forget_transports,
)

# A reload from code keeps the settings, so memos derived from old routers drop.
_ROUTER_RELOADED: Final[tuple[Callable[..., None], ...]] = (
    forget_watch_state,
    forget_shared_layouts,
    forget_page_roots,
    forget_manager_page_roots,
    forget_dep_caches,
    forget_object_rows,
    forget_seo_sources,
    forget_translated_urls,
    forget_scripts,
)


def _connect(signal: Signal, receivers: tuple[Callable[..., None], ...]) -> None:
    """Connect each receiver once, in order, however often `ready` runs."""
    for receiver in receivers:
        uid = f"{receiver.__module__}.{receiver.__qualname__}"
        signal.connect(receiver, dispatch_uid=uid)


class NextFrameworkConfig(AppConfig):
    """Connect autoreload, template tag builtins, and filesystem watches."""

    name = "next"
    verbose_name = "Next Django Framework"

    @override
    def ready(self) -> None:
        """Register checks, connect every receiver, install hooks, compose the ports.

        The resolver and the ports come before the installs, which import user modules.
        """
        _register_checks()
        _connect(setting_changed, _SETTING_CHANGED)
        _connect(settings_reloaded, _SETTINGS_RELOADED)
        _connect(router_reloaded, _ROUTER_RELOADED)
        apply_resolver_setting()
        action_owners_slot.set(ActionOwnersImpl())
        form_routes_slot.set(form_action_manager)
        page_scan_slot.set(PageScanImpl())
        partial_shaper_slot.set(PartialShaperImpl())
        realtime_negotiation_slot.set(RealtimeNegotiationImpl())
        router_access_slot.set(RouterAccessImpl())
        page_scripts_slot.set(PageScriptsImpl())
        seo_routes_slot.set(SeoRoutesImpl())
        seo_routes_version.move()
        static_assets_slot.set(StaticAssetsImpl())
        autoreload.install()
        templates.install()
        staticfiles.install()
        autodiscover.install()
        components.install()


__all__ = ["NextFrameworkConfig"]
