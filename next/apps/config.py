"""`AppConfig` for next-dj that wires reloader, templates, finders, and components."""

from __future__ import annotations

from typing import override

from django.apps import AppConfig

from next.checks import register_all as _register_checks
from next.forms.autodiscover import autodiscover_forms
from next.ports import partial_shaper_slot

from . import autoreload, components, staticfiles, templates


class NextFrameworkConfig(AppConfig):
    """Connect autoreload, template tag builtins, and filesystem watches."""

    name = "next"
    verbose_name = "Next Django Framework"

    @override
    def ready(self) -> None:
        """Register checks, install every startup hook, and compose the ports."""
        _register_checks()
        autoreload.install()
        templates.install()
        staticfiles.install()
        components.install()
        autodiscover_forms()
        # Deferred so importing next.apps stays free of next.partial until
        # Django has registered every AppConfig.
        from next.partial.shaper import PartialShaperImpl  # noqa: PLC0415

        partial_shaper_slot.set(PartialShaperImpl())


__all__ = ["NextFrameworkConfig"]
