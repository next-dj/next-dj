"""Patch Django's autoreload to use next-dj's reloader and watch specs.

Django does not expose a setting for overriding the reloader class, so
this module still swaps `autoreload.StatReloader`. The swap is kept
idempotent and records the class it replaces so tests (or other
packages) can restore it. A warning is logged if another library has
replaced `StatReloader` with a class that is not a `StatReloader`
subclass, which suggests an incompatible override and makes our patch
unsafe to apply.
"""

from __future__ import annotations

import logging

from django.utils import autoreload
from django.utils.autoreload import StatReloader, autoreload_started

from next.server import NextStatReloader, iter_all_autoreload_watch_specs


logger = logging.getLogger(__name__)

_ORIGINAL_STAT_RELOADER: type[StatReloader] | None = None
_WATCHER_CONNECTED = False


def install() -> None:
    """Swap `StatReloader` for `NextStatReloader` and wire watch specs.

    Safe to call more than once: subsequent calls are no-ops once the
    current `autoreload.StatReloader` is already our subclass or one of
    its descendants.
    """
    global _ORIGINAL_STAT_RELOADER, _WATCHER_CONNECTED  # noqa: PLW0603

    current = autoreload.StatReloader
    if issubclass(current, NextStatReloader):
        pass
    elif issubclass(current, StatReloader):
        _ORIGINAL_STAT_RELOADER = current
        autoreload.StatReloader = NextStatReloader  # type: ignore[misc]
    else:
        logger.warning(
            "autoreload.StatReloader has been replaced by %r which is not a "
            "StatReloader subclass; next-dj will not override it.",
            current,
        )

    if not _WATCHER_CONNECTED:
        autoreload_started.connect(_watch_next_filesystem)
        _WATCHER_CONNECTED = True


def uninstall() -> None:
    """Restore the previous `StatReloader` class if `install()` swapped it."""
    global _ORIGINAL_STAT_RELOADER, _WATCHER_CONNECTED  # noqa: PLW0603

    if _ORIGINAL_STAT_RELOADER is not None:
        autoreload.StatReloader = _ORIGINAL_STAT_RELOADER  # type: ignore[misc]
        _ORIGINAL_STAT_RELOADER = None
    if _WATCHER_CONNECTED:
        autoreload_started.disconnect(_watch_next_filesystem)
        _WATCHER_CONNECTED = False


def _watch_next_filesystem(sender: object, **_: object) -> None:
    for path, glob in iter_all_autoreload_watch_specs():
        sender.watch_dir(path, glob)  # type: ignore[attr-defined]


__all__ = ["install", "uninstall"]
