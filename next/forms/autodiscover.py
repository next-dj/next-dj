"""Import every `<app>.forms` module so shared forms register on startup."""

from __future__ import annotations

import importlib

from django.apps import apps
from django.utils.module_loading import module_has_submodule

from next.conf import next_framework_settings


_discovered: set[str] = set()


def autodiscover_forms() -> None:
    """Import the `forms` submodule of each installed app once.

    A missing `forms` module is skipped. A module that exists but raises
    on import propagates, as that is a user error.
    """
    if not next_framework_settings.FORM_AUTODISCOVER:
        return
    for app_config in apps.get_app_configs():
        target = f"{app_config.name}.forms"
        if target in _discovered:
            continue
        try:
            importlib.import_module(target)
        except ImportError:
            if module_has_submodule(app_config.module, "forms"):
                raise
            continue
        _discovered.add(target)


def clear_discovered() -> None:
    """Reset the discovered-module guard for test isolation."""
    _discovered.clear()


__all__ = ["autodiscover_forms", "clear_discovered"]
