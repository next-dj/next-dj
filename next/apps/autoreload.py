"""Patch Django's autoreload to use next-dj's reloader and watch specs."""

from __future__ import annotations

from django.utils import autoreload
from django.utils.autoreload import autoreload_started

from next.server import NextStatReloader, iter_all_autoreload_watch_specs


def install() -> None:
    """Replace `StatReloader` and connect watch specs to `autoreload_started`."""
    autoreload.StatReloader = NextStatReloader  # type: ignore[misc]
    autoreload_started.connect(_watch_next_filesystem)


def _watch_next_filesystem(sender: object, **_: object) -> None:
    for path, glob in iter_all_autoreload_watch_specs():
        sender.watch_dir(path, glob)  # type: ignore[attr-defined]


__all__ = ["install"]
