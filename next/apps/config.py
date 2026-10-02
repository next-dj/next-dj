"""`AppConfig` for next-dj that wires reloader, templates, finders, and components."""

from __future__ import annotations

from typing import override

from django.apps import AppConfig

from next.checks import register_all as _register_checks
from next.conf.signals import settings_reloaded
from next.consent.manager import forget_consent_settings
from next.deps.resolver import apply_resolver_setting, forget_dep_caches
from next.forms.autodiscover import autodiscover_forms
from next.pages.loaders import forget_page_roots
from next.pages.metadata.hreflang import forget_translated_urls
from next.pages.ports import PageScanImpl
from next.pages.watch import forget_watch_state
from next.partial.ports import PartialShaperImpl
from next.ports import (
    page_scan_slot,
    page_scripts_slot,
    partial_shaper_slot,
    router_access_slot,
    seo_routes_slot,
    static_assets_slot,
)
from next.scripts.manager import forget_scripts
from next.scripts.ports import PageScriptsImpl
from next.seo.manager import forget_seo_sources
from next.seo.ports import SeoRoutesImpl
from next.static.manager import forget_manager_page_roots
from next.static.ports import StaticAssetsImpl
from next.urls.manager import seo_routes_version
from next.urls.ports import RouterAccessImpl
from next.urls.signals import router_reloaded

from . import autoreload, components, staticfiles, templates


class NextFrameworkConfig(AppConfig):
    """Connect autoreload, template tag builtins, and filesystem watches."""

    name = "next"
    verbose_name = "Next Django Framework"

    @override
    def ready(self) -> None:
        """Register checks, install every startup hook, and compose the ports."""
        _register_checks()
        # A reload from code replaces the routers the URL resolver serves
        # without touching settings, and every memo of what those routers
        # report has to go with them or the layers answer for two generations.
        router_reloaded.connect(forget_watch_state)
        router_reloaded.connect(forget_page_roots)
        router_reloaded.connect(forget_manager_page_roots)
        router_reloaded.connect(forget_dep_caches)
        router_reloaded.connect(forget_seo_sources)
        router_reloaded.connect(forget_translated_urls)
        router_reloaded.connect(forget_scripts, dispatch_uid="next.scripts.router")
        settings_reloaded.connect(forget_scripts, dispatch_uid="next.scripts.settings")
        settings_reloaded.connect(
            forget_consent_settings, dispatch_uid="next.consent.settings"
        )
        # Ahead of every install, because component discovery and form autodiscovery
        # import user modules that must see the configured resolver, not the base one.
        apply_resolver_setting()
        # For the same reason, and so a discovery failure leaves no process behind
        # with an unbound port. The static handle stays lazy, because binding it
        # stores the handle rather than reading through it.
        page_scan_slot.set(PageScanImpl())
        partial_shaper_slot.set(PartialShaperImpl())
        router_access_slot.set(RouterAccessImpl())
        page_scripts_slot.set(PageScriptsImpl())
        seo_routes_slot.set(SeoRoutesImpl())
        seo_routes_version.move()
        static_assets_slot.set(StaticAssetsImpl())
        autoreload.install()
        templates.install()
        staticfiles.install()
        components.install()
        autodiscover_forms()


__all__ = ["NextFrameworkConfig"]
