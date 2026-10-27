"""Framework-agnostic helpers for testing next-dj apps.

Pure-Python utilities for Django `TestCase`, unittest and pytest.
"""

from __future__ import annotations

from .actions import build_form_for, resolve_action_url
from .capture import (
    SignalEvent,
    SignalRecorder,
    capture_framework_signals,
    capture_signals,
)
from .client import NextClient, PartialEnvelope, envelope_of
from .deps import make_resolution_context, resolve_call
from .html import (
    assert_has_class,
    assert_missing_class,
    find_anchor,
    find_form,
    form_action,
    form_fields,
    hidden_fields,
    init_payload,
)
from .isolation import (
    reset_component_templates,
    reset_components,
    reset_failure_logs,
    reset_form_actions,
    reset_form_registration_state,
    reset_page_cache,
    reset_registries,
    reset_scripts,
    reset_seo,
)
from .loaders import clear_loaded_dirs, eager_load_components, eager_load_pages
from .metadata import assert_metadata
from .patching import (
    StaticCollectorProxy,
    override_component_backends,
    override_dependency,
    override_form_action,
    override_next_settings,
    override_provider,
    patch_static_collector,
)
from .realtime import (
    MemoryPort,
    PublishedMessages,
    SocketTicket,
    SseEvent,
    SseReader,
    WebSocketCommunicator,
    aissue_socket_ticket,
    capture_published,
    issue_socket_ticket,
    memory_broker,
    override_websocket_mount,
)
from .rendering import render_component_by_name, render_page
from .seo import SitemapUrl, parse_sitemap


__all__ = [
    "MemoryPort",
    "NextClient",
    "PartialEnvelope",
    "PublishedMessages",
    "SignalEvent",
    "SignalRecorder",
    "SitemapUrl",
    "SocketTicket",
    "SseEvent",
    "SseReader",
    "StaticCollectorProxy",
    "WebSocketCommunicator",
    "aissue_socket_ticket",
    "assert_has_class",
    "assert_metadata",
    "assert_missing_class",
    "build_form_for",
    "capture_framework_signals",
    "capture_published",
    "capture_signals",
    "clear_loaded_dirs",
    "eager_load_components",
    "eager_load_pages",
    "envelope_of",
    "find_anchor",
    "find_form",
    "form_action",
    "form_fields",
    "hidden_fields",
    "init_payload",
    "issue_socket_ticket",
    "make_resolution_context",
    "memory_broker",
    "override_component_backends",
    "override_dependency",
    "override_form_action",
    "override_next_settings",
    "override_provider",
    "override_websocket_mount",
    "parse_sitemap",
    "patch_static_collector",
    "render_component_by_name",
    "render_page",
    "reset_component_templates",
    "reset_components",
    "reset_failure_logs",
    "reset_form_actions",
    "reset_form_registration_state",
    "reset_page_cache",
    "reset_registries",
    "reset_scripts",
    "reset_seo",
    "resolve_action_url",
    "resolve_call",
]
