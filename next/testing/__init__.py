"""Framework-agnostic helpers for testing next-dj apps.

The public surface is a small set of pure-Python utilities that work
with Django `TestCase`, stdlib `unittest`, and pytest. Nothing in this
package imports pytest.
"""

from __future__ import annotations

from .actions import build_form_for, resolve_action_url
from .client import NextClient
from .isolation import reset_components, reset_form_actions, reset_registries
from .loaders import clear_loaded_dirs, eager_load_pages
from .signals import SignalEvent, SignalRecorder


__all__ = [
    "NextClient",
    "SignalEvent",
    "SignalRecorder",
    "build_form_for",
    "clear_loaded_dirs",
    "eager_load_pages",
    "reset_components",
    "reset_form_actions",
    "reset_registries",
    "resolve_action_url",
]
