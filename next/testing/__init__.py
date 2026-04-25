"""Framework-agnostic helpers for testing next-dj apps.

The public surface is a small set of pure-Python utilities that work
with Django `TestCase`, stdlib `unittest`, and pytest. Nothing in this
package imports pytest.
"""

from __future__ import annotations

from .actions import build_form_for, resolve_action_url
from .client import NextClient
from .deps import make_resolution_context, resolve_call
from .html import assert_has_class, assert_missing_class, find_anchor
from .isolation import (
    reset_components,
    reset_form_actions,
    reset_page_cache,
    reset_registries,
)
from .loaders import clear_loaded_dirs, eager_load_pages
from .patching import (
    StaticCollectorProxy,
    override_component_backends,
    override_dependency,
    override_form_action,
    override_next_settings,
    override_provider,
    patch_static_collector,
)
from .rendering import render_component_by_name, render_page
from .signals import (
    SignalEvent,
    SignalRecorder,
    capture_framework_signals,
    capture_signals,
)


__all__ = [
    "NextClient",
    "SignalEvent",
    "SignalRecorder",
    "StaticCollectorProxy",
    "assert_has_class",
    "assert_missing_class",
    "build_form_for",
    "capture_framework_signals",
    "capture_signals",
    "clear_loaded_dirs",
    "eager_load_pages",
    "find_anchor",
    "make_resolution_context",
    "override_component_backends",
    "override_dependency",
    "override_form_action",
    "override_next_settings",
    "override_provider",
    "patch_static_collector",
    "render_component_by_name",
    "render_page",
    "reset_components",
    "reset_form_actions",
    "reset_page_cache",
    "reset_registries",
    "resolve_action_url",
    "resolve_call",
]
