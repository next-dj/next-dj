"""Django signals emitted by the configuration layer.

Nothing here reads the merged settings, so the reload module imports this one.
"""

import logging

from django.dispatch import Signal

from next.introspect import callable_name


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
