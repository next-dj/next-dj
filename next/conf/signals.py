"""Django signals emitted by the configuration layer.

`settings_reloaded` fires after `NextFrameworkSettings.reload` drops its caches,
through `dispatch_settings_reloaded`, which runs every receiver before it lets an
error out. Package-level managers subscribe to it and reset their own state when the
merged settings change. The module also wires the Django `setting_changed` signal, so
`override_settings` in a test triggers the reload path on its own.
"""

import logging

from django.core.signals import setting_changed
from django.dispatch import Signal

from next.utils import callable_name

from .defaults import USER_SETTING
from .settings import next_framework_settings


logger = logging.getLogger(__name__)

settings_reloaded: Signal = Signal()
"""Emitted when `NextFrameworkSettings` caches have been dropped."""


def dispatch_settings_reloaded(sender: type) -> None:
    """Run every `settings_reloaded` receiver, then raise what one of them raised.

    A receiver that validates a settings value raises for a bad one, and the managers
    behind it still have to drop what they built from the settings just replaced. The
    robust send has run them all by the time the first error leaves here, and a failure
    behind that one is logged rather than lost.
    """
    first: Exception | None = None
    for receiver, response in settings_reloaded.send_robust(sender=sender):
        if not isinstance(response, Exception):
            continue
        if first is None:
            first = response
            continue
        logger.error(
            "settings_reloaded receiver %s failed behind the error this reload raises.",
            callable_name(receiver),
            exc_info=response,
        )
    if first is not None:
        raise first


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Reload framework settings when Django reports a matching change."""
    if setting == USER_SETTING:
        next_framework_settings.reload()


setting_changed.connect(_on_setting_changed)
