"""Development-server helpers and autoreload integration.

`NextStatReloader` replaces Django's `StatReloader` and the watch helpers feed it specs.
"""

from __future__ import annotations

from . import signals
from .autoreload import NextStatReloader
from .roots import get_framework_filesystem_roots_for_linking
from .watcher import iter_all_autoreload_watch_specs, register_autoreload_watch_spec


__all__ = [
    "NextStatReloader",
    "get_framework_filesystem_roots_for_linking",
    "iter_all_autoreload_watch_specs",
    "register_autoreload_watch_spec",
    "signals",
]
