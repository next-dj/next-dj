"""Merged view of `settings.NEXT_FRAMEWORK` with framework defaults.

The `NextFrameworkSettings` class reads the user mapping lazily and merges it with
`DEFAULTS` on first access. Merge results are cached until `reload()` drops the cache
and emits `settings_reloaded`. Package managers that depend on the merged values
subscribe to that signal and reset their own state. The Django `setting_changed`
receiver lives here too, because it reloads the singleton this module owns. The merge
policy itself lives in `merge`.
"""

from __future__ import annotations

from typing import Any, ClassVar, override

from django.conf import settings
from django.core.signals import setting_changed

from .defaults import DEFAULTS, USER_SETTING
from .imports import clear_import_cache
from .merge import BOOL_KEYS, LIST_KEYS, STR_KEYS, merge_user_settings
from .signals import dispatch_settings_reloaded


class NextFrameworkSettings:
    """Lazy merged view of top-level keys declared in `DEFAULTS`."""

    DEFAULTS: ClassVar[dict[str, Any]] = DEFAULTS
    LIST_KEYS: ClassVar[frozenset[str]] = LIST_KEYS
    STR_KEYS: ClassVar[frozenset[str]] = STR_KEYS
    BOOL_KEYS: ClassVar[frozenset[str]] = BOOL_KEYS

    def __init__(self) -> None:
        """Initialise empty merge and attribute caches."""
        self._merged_cache: dict[str, Any] | None = None
        self._attr_value_cache: dict[str, Any] = {}

    def reload(self) -> None:
        """Drop merge and import caches and emit the reload signal.

        Package-level managers listen to `settings_reloaded` and reset their own state.
        This method only clears caches owned by the settings object itself.
        """
        self._merged_cache = None
        self._attr_value_cache.clear()
        clear_import_cache()
        dispatch_settings_reloaded(type(self))

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
        return merge_user_settings(self.DEFAULTS, user)

    def __getattr__(self, attr: str) -> Any:  # noqa: ANN401
        """Return merged values for keys declared in `DEFAULTS`.

        `DEFAULTS` is open for third-party keys and every value carries the
        shape of its own key, so one accessor cannot name a narrower return.
        `Any` is what lets each read site re-validate the shape it needs.
        """
        if attr in self._attr_value_cache:
            return self._attr_value_cache[attr]
        if attr not in self.DEFAULTS:
            allowed = ", ".join(sorted(self.DEFAULTS))
            msg = f"Invalid Next framework setting: {attr!r}. Allowed keys: {allowed}."
            raise AttributeError(msg)
        val = self._merged()[attr]
        self._attr_value_cache[attr] = val
        return val

    @override
    def __setattr__(self, name: str, value: object) -> None:
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


def fail_loudly() -> bool:
    """Whether a load failure is raised at its caller rather than logged away.

    Every fail-loud path reads this one predicate, so `STRICT_LOADING` and
    `DEBUG` cannot drift apart between the page loader and the component tag.
    """
    return bool(next_framework_settings.STRICT_LOADING or settings.DEBUG)


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Reload framework settings when Django reports a matching change."""
    if setting == USER_SETTING:
        next_framework_settings.reload()


setting_changed.connect(_on_setting_changed)
