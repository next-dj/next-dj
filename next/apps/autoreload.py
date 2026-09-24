"""Patch Django's autoreload to use next-dj's reloader and watch specs.

Django exposes no setting for the reloader class, so the swap patches `StatReloader`.
"""

from __future__ import annotations

import logging

from django.utils import autoreload
from django.utils.autoreload import StatReloader, autoreload_started

from next.server import NextStatReloader, iter_all_autoreload_watch_specs


logger = logging.getLogger(__name__)


class _PatchState:
    """Install-time state mutated in place so the swap never rebinds a global."""

    def __init__(self) -> None:
        """Start with no recorded reloader and the watcher disconnected."""
        self.original_reloader: type[StatReloader] | None = None
        self.watcher_connected = False


_state = _PatchState()


def install() -> None:
    """Swap `StatReloader` for `NextStatReloader` and wire watch specs.

    Safe to call more than once: subsequent calls are no-ops once the current
    `autoreload.StatReloader` is already our subclass or one of its descendants.
    """
    current = autoreload.StatReloader
    if issubclass(current, NextStatReloader):
        pass
    elif issubclass(current, StatReloader):
        _state.original_reloader = current
        autoreload.StatReloader = NextStatReloader  # type: ignore[misc]
    else:
        logger.warning(
            "autoreload.StatReloader has been replaced by %r which is not a "
            "StatReloader subclass, so next-dj will not override it.",
            current,
        )

    if not _state.watcher_connected:
        autoreload_started.connect(_watch_next_filesystem)
        _state.watcher_connected = True


def uninstall() -> None:
    """Restore the previous `StatReloader` class if `install()` swapped it."""
    if _state.original_reloader is not None:
        autoreload.StatReloader = _state.original_reloader  # type: ignore[misc]
        _state.original_reloader = None
    if _state.watcher_connected:
        autoreload_started.disconnect(_watch_next_filesystem)
        _state.watcher_connected = False


def _watch_next_filesystem(sender: object, **kwargs) -> None:
    for path, glob in iter_all_autoreload_watch_specs():
        sender.watch_dir(path, glob)  # type: ignore[attr-defined]


__all__ = ["install", "uninstall"]
