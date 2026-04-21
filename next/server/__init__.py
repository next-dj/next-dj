"""Development-server helpers and autoreload integration.

This package replaces Django's default `StatReloader` with
`NextStatReloader`, exposes helpers that contribute watch specs to the
file watcher, and lists filesystem roots for tooling that needs stable
paths.
"""

from __future__ import annotations

from . import checks, signals
from .autoreload import NextStatReloader
from .roots import get_framework_filesystem_roots_for_linking
from .watcher import (
    FilesystemWatchContributor,
    iter_all_autoreload_watch_specs,
    register_autoreload_watch_spec,
)


__all__ = [
    "FilesystemWatchContributor",
    "NextStatReloader",
    "checks",
    "get_framework_filesystem_roots_for_linking",
    "iter_all_autoreload_watch_specs",
    "register_autoreload_watch_spec",
    "signals",
]
