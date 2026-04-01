"""Provide merged ``settings.NEXT_FRAMEWORK`` with framework ``DEFAULTS``.

Imported classes stay cached until ``NextFrameworkSettings.reload()`` clears caches.
"""

from __future__ import annotations

import copy
import importlib
from typing import Any, ClassVar

from django.conf import settings
from django.core.signals import setting_changed
from django.utils.module_loading import import_string


USER_SETTING = "NEXT_FRAMEWORK"

_import_class_cache: dict[str, type[Any]] = {}


def import_class_cached(dotted_path: str) -> type[Any]:
    """Import a class by dotted path and cache it until ``reload()``."""
    if dotted_path not in _import_class_cache:
        _import_class_cache[dotted_path] = import_string(dotted_path)
    return _import_class_cache[dotted_path]


class NextFrameworkSettings:
    """Lazy merged view of ``NEXT_FRAMEWORK`` keys defined in ``DEFAULTS``."""

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "DEFAULT_PAGE_BACKENDS": [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "DIRS": [],
                "APP_DIRS": True,
                "PAGES_DIR": "pages",
                "OPTIONS": {
                    "context_processors": [],
                },
            },
        ],
        "URL_NAME_TEMPLATE": "page_{name}",
        "DEFAULT_COMPONENT_BACKENDS": [
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_components",
            },
        ],
    }

    IMPORT_STRINGS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        """Empty merge and attribute caches."""
        self._merged_cache: dict[str, Any] | None = None
        self._attr_value_cache: dict[str, Any] = {}

    def reload(self) -> None:
        """Drop caches and reload URL and component backends."""
        self._merged_cache = None
        self._attr_value_cache.clear()

        _import_class_cache.clear()

        urls_mod = importlib.import_module("next.urls")
        components_mod = importlib.import_module("next.components")
        urls_mod.router_manager._reload_config()
        components_mod.components_manager._reload_config()

    def _raw_user(self) -> dict[str, Any] | None:
        raw = getattr(settings, USER_SETTING, None)
        if raw is None or raw == {}:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _merged(self) -> dict[str, Any]:
        if self._merged_cache is None:
            self._merged_cache = self._build_flat_merged(self._raw_user())
        return self._merged_cache

    def _build_flat_merged(self, user: dict[str, Any] | None) -> dict[str, Any]:
        out = copy.deepcopy(self.DEFAULTS)
        if not user:
            return out
        user = dict(user)
        for key in self.DEFAULTS:
            if key not in user:
                continue
            raw = user[key]
            if key == "URL_NAME_TEMPLATE" and isinstance(raw, str):
                out[key] = raw
            elif key in (
                "DEFAULT_PAGE_BACKENDS",
                "DEFAULT_COMPONENT_BACKENDS",
            ) and isinstance(raw, list):
                out[key] = copy.deepcopy(raw)
        return out

    def __getattr__(self, attr: str) -> Any:  # noqa: ANN401
        """Return merged values for keys that exist in ``DEFAULTS``."""
        if attr in self._attr_value_cache:
            return self._attr_value_cache[attr]
        if attr not in self.DEFAULTS:
            allowed = ", ".join(sorted(self.DEFAULTS))
            msg = f"Invalid Next framework setting: {attr!r}. Allowed keys: {allowed}."
            raise AttributeError(msg)
        val = self._merged()[attr]
        self._attr_value_cache[attr] = val
        return val

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Allow only internal caches."""
        if name in {"_merged_cache", "_attr_value_cache"}:
            super().__setattr__(name, value)
            return
        if name in self.DEFAULTS:
            msg = (
                f"Setting {name!r} cannot be assigned. Update "
                f"settings.{USER_SETTING} instead."
            )
            raise AttributeError(msg)
        super().__setattr__(name, value)


def perform_import(val: Any, setting_name: str) -> Any:  # noqa: ANN401
    """Resolve ``val`` when it is a dotted import path (see ``IMPORT_STRINGS``)."""
    if val is None or not isinstance(val, str):
        return val
    try:
        return import_class_cached(val)
    except ImportError as e:
        detail = f"{e!s}"
        msg = (
            f"Could not import {val!r} for Next framework setting "
            f"{setting_name!r}: {detail}"
        )
        raise ImportError(msg) from e


next_framework_settings = NextFrameworkSettings()


def _on_setting_changed(*, setting: str, **_kwargs: object) -> None:
    if setting == USER_SETTING:
        next_framework_settings.reload()


setting_changed.connect(_on_setting_changed)
