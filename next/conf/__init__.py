"""Merge user `NEXT_FRAMEWORK` settings with framework defaults.

The public surface is `NextFrameworkSettings`, `next_framework_settings`,
`perform_import`, `import_class_cached`, and the `settings_reloaded`
signal exposed through the `signals` submodule. Internal helpers live in
`defaults`, `imports`, and `settings` and are reachable through deep
imports when user code needs them.
"""

from __future__ import annotations

from . import checks, signals
from .defaults import DEFAULTS, USER_SETTING
from .helpers import extend_default_backend
from .imports import IMPORT_STRINGS, import_class_cached, perform_import
from .settings import NextFrameworkSettings, next_framework_settings


__all__ = [
    "DEFAULTS",
    "IMPORT_STRINGS",
    "USER_SETTING",
    "NextFrameworkSettings",
    "checks",
    "extend_default_backend",
    "import_class_cached",
    "next_framework_settings",
    "perform_import",
    "signals",
]
