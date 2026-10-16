"""Import the conventional modules of every installed app when the framework starts."""

from __future__ import annotations

from typing import Final

from django.utils.module_loading import autodiscover_modules

from next.conf import next_framework_settings


MODULES: Final[tuple[tuple[str, str], ...]] = (
    ("deps", "DEPS_AUTODISCOVER"),
    ("permissions", "PERMISSIONS_AUTODISCOVER"),
    ("forms", "FORM_AUTODISCOVER"),
    ("signals", "SIGNALS_AUTODISCOVER"),
)
"""App module names in import order, each paired with the bool setting enabling it.

A new convention is one pair here, plus its key in `DEFAULTS` and `BOOL_KEYS`.
"""


def install() -> None:
    """Import every enabled module from all installed apps, one module name at a time.

    A name finishes across all apps first, so a form sees the providers of every app.
    """
    for module, key in MODULES:
        if getattr(next_framework_settings, key):
            autodiscover_modules(module)


__all__ = ["MODULES", "install"]
