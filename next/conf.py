"""Next framework settings ``NEXT_FRAMEWORK`` in Django ``settings``."""

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
    """Resolve ``dotted_path`` to a class.

    Result is cached until :meth:`NextFrameworkSettings.reload`.
    """
    if dotted_path not in _import_class_cache:
        _import_class_cache[dotted_path] = import_string(dotted_path)
    return _import_class_cache[dotted_path]


def _clear_import_class_cache() -> None:
    _import_class_cache.clear()


class NextFrameworkSettings:
    """Merged ``settings.NEXT_FRAMEWORK`` with :attr:`DEFAULTS` (attribute access)."""

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "DEFAULT_PAGE_ROUTERS": [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
                "PAGES_DIR": "pages",
                "OPTIONS": {
                    "COMPONENTS_DIR": "_components",
                },
            },
        ],
        "URL_NAME_TEMPLATE": "page_{name}",
        "DEFAULT_COMPONENT_BACKENDS": [
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "APP_DIRS": True,
                "OPTIONS": {
                    "COMPONENTS_DIR": "_components",
                    "PAGES_DIR": "pages",
                },
            },
        ],
    }

    #: Top-level keys whose values are dotted paths resolved via :func:`import_string`.
    IMPORT_STRINGS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        """Initialize merge cache and per-attribute value cache."""
        self._merged_cache: dict[str, Any] | None = None
        self._attr_value_cache: dict[str, Any] = {}

    def reload(self) -> None:
        """Clear caches and refresh router and component manager state."""
        self._merged_cache = None
        self._attr_value_cache.clear()
        _clear_import_class_cache()
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
        if not user:
            return copy.deepcopy(self.DEFAULTS)
        out = copy.deepcopy(self.DEFAULTS)
        for key in self.DEFAULTS:
            if key not in user:
                continue
            raw = user[key]
            if key == "URL_NAME_TEMPLATE" and isinstance(raw, str):
                out[key] = raw
            elif key in (
                "DEFAULT_PAGE_ROUTERS",
                "DEFAULT_COMPONENT_BACKENDS",
            ) and isinstance(raw, list):
                out[key] = copy.deepcopy(raw)
        return out

    def __getattr__(self, attr: str) -> Any:  # noqa: ANN401
        """Return merged setting ``attr`` (cached after first access)."""
        if attr in self._attr_value_cache:
            return self._attr_value_cache[attr]
        if attr not in self.DEFAULTS:
            allowed = ", ".join(sorted(self.DEFAULTS))
            msg = f"Invalid Next framework setting: {attr!r}. Allowed keys: {allowed}."
            raise AttributeError(msg)
        val: Any = self._merged()[attr]
        self._attr_value_cache[attr] = val
        return val

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Allow internal slots; forbid assigning top-level framework keys."""
        if name in {"_merged_cache", "_attr_value_cache"}:
            super().__setattr__(name, value)
            return
        if name in self.DEFAULTS:
            msg = (
                f"Setting {name!r} cannot be assigned; update "
                f"settings.{USER_SETTING} instead."
            )
            raise AttributeError(msg)
        super().__setattr__(name, value)


def perform_import(val: Any, setting_name: str) -> Any:  # noqa: ANN401
    """Resolve ``val`` when it is a dotted path (see :attr:`IMPORT_STRINGS`)."""
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
