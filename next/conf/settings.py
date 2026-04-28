"""Merged view of `settings.NEXT_FRAMEWORK` with framework defaults.

The `NextFrameworkSettings` class reads the user mapping lazily and
merges it with `DEFAULTS` on first access. Merge results are cached
until `reload()` drops the cache and emits `settings_reloaded`. Package
managers that depend on the merged values subscribe to that signal and
reset their own state.
"""

from __future__ import annotations

import copy
from typing import Any, ClassVar

from django.conf import settings

from .defaults import DEFAULTS, USER_SETTING
from .imports import clear_import_cache


class NextFrameworkSettings:
    """Lazy merged view of top-level keys declared in `DEFAULTS`."""

    DEFAULTS: ClassVar[dict[str, Any]] = DEFAULTS
    IMPORT_STRINGS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        """Initialise empty merge and attribute caches."""
        self._merged_cache: dict[str, Any] | None = None
        self._attr_value_cache: dict[str, Any] = {}

    def reload(self) -> None:
        """Drop merge and import caches and emit the reload signal.

        Package-level managers listen to `settings_reloaded` and reset
        their own state. This method only clears caches owned by the
        settings object itself.
        """
        self._merged_cache = None
        self._attr_value_cache.clear()
        clear_import_cache()
        from .signals import settings_reloaded  # noqa: PLC0415

        settings_reloaded.send(sender=type(self))

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

    _LIST_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "DEFAULT_PAGE_BACKENDS",
            "DEFAULT_COMPONENT_BACKENDS",
            "DEFAULT_STATIC_BACKENDS",
            "DEFAULT_FORM_ACTION_BACKENDS",
            "TEMPLATE_LOADERS",
        }
    )
    _BOOL_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "STRICT_CONTEXT",
            "LAZY_COMPONENT_MODULES",
        }
    )

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
            elif (key in self._LIST_KEYS and isinstance(raw, list)) or (
                key == "NEXT_JS_OPTIONS" and isinstance(raw, dict)
            ):
                out[key] = copy.deepcopy(raw)
            elif key in self._BOOL_KEYS:
                out[key] = bool(raw)
            elif key == "JS_CONTEXT_SERIALIZER" and (
                raw is None or isinstance(raw, str)
            ):
                out[key] = raw
        return out

    def __getattr__(self, attr: str) -> Any:  # noqa: ANN401
        """Return merged values for keys declared in `DEFAULTS`."""
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
        """Allow only internal cache attributes and raise for declared keys."""
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


next_framework_settings: NextFrameworkSettings = NextFrameworkSettings()
