"""Merge user `NEXT_FRAMEWORK` settings with framework defaults."""

from __future__ import annotations

from . import checks, signals
from .defaults import DEFAULTS, USER_SETTING
from .helpers import extend_default_backend
from .imports import import_class_cached
from .settings import NextFrameworkSettings, fail_loudly, next_framework_settings


__all__ = [
    "DEFAULTS",
    "USER_SETTING",
    "NextFrameworkSettings",
    "checks",
    "extend_default_backend",
    "fail_loudly",
    "import_class_cached",
    "next_framework_settings",
    "signals",
]
