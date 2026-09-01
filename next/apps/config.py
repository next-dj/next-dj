"""`AppConfig` for next-dj that wires reloader, templates, finders, and components."""

from __future__ import annotations

from typing import override

from django.apps import AppConfig

from next.checks import register_all as _register_checks
from next.forms.autodiscover import autodiscover_forms
from next.pages.loaders import forget_page_roots
from next.pages.watch import forget_watch_state
from next.ports import partial_shaper_slot
from next.static.manager import forget_manager_page_roots
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
        autoreload.install()
        templates.install()
        staticfiles.install()
        components.install()
        autodiscover_forms()
        # Deferred so importing next.apps stays free of next.partial.
        from next.partial.shaper import PartialShaperImpl  # noqa: PLC0415

        partial_shaper_slot.set(PartialShaperImpl())


__all__ = ["NextFrameworkConfig"]
