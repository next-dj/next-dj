"""`AppConfig` for next-dj that wires reloader, templates, finders, and components."""

from __future__ import annotations

from typing import override

from django.apps import AppConfig

from next.checks import register_all as _register_checks
from next.forms.autodiscover import autodiscover_forms

from . import autoreload, components, staticfiles, templates


class NextFrameworkConfig(AppConfig):
    """Connect autoreload, template tag builtins, and filesystem watches."""

    name = "next"
    verbose_name = "Next Django Framework"

    @override
    def ready(self) -> None:
        """Register checks and install every startup hook."""
        _register_checks()
        autoreload.install()
        templates.install()
        staticfiles.install()
        components.install()
        autodiscover_forms()


__all__ = ["NextFrameworkConfig"]
