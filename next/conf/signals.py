"""Django signals emitted by the configuration layer.

`settings_reloaded` fires after `NextFrameworkSettings.reload` drops its caches,
through `dispatch_settings_reloaded`, which runs every receiver before it lets an
error out. Package-level managers subscribe to it and reset their own state when the
merged settings change. Nothing here reads the merged settings, so the module the
reload lives in imports this one and not the other way round.
"""

import logging

from django.dispatch import Signal

from next.utils import callable_name


logger = logging.getLogger(__name__)

settings_reloaded: Signal = Signal()
"""Emitted when `NextFrameworkSettings` caches have been dropped."""


def dispatch_settings_reloaded(sender: type) -> None:
    """Run every `settings_reloaded` receiver, then raise what one of them raised.

    Every receiver still has to drop what it built when another one raises, so the
    robust send runs them all and the first error leaves only after that.
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
