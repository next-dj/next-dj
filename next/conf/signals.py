"""Django signals emitted by the configuration layer.

`settings_reloaded` fires after `NextFrameworkSettings.reload` drops its
caches. Package-level managers subscribe to this signal and reset their
own state when the merged settings change. The module also wires the
Django `setting_changed` signal so that tests using `override_settings`
trigger the reload path automatically.
"""

from __future__ import annotations

from django.core.signals import setting_changed
from django.dispatch import Signal

from .defaults import USER_SETTING
from .settings import next_framework_settings


settings_reloaded: Signal = Signal()
"""Emitted when `NextFrameworkSettings` caches have been dropped."""


def _on_setting_changed(*, setting: str, **_kwargs: object) -> None:
    """Reload framework settings when Django reports a matching change."""
    if setting == USER_SETTING:
        next_framework_settings.reload()


setting_changed.connect(_on_setting_changed)
