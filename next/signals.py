"""Aggregate re-export of every signal emitted by the framework.

Signals also live on their owning subpackage (for example ``next.forms.signals``).
Import from here when one module subscribes to several subsystems and prefers one
import path.
"""

from __future__ import annotations

from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)
from next.conf.signals import settings_reloaded
from next.deps.signals import provider_registered
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_access_denied,
    form_validation_failed,
    wizard_completed,
    wizard_step_submitted,
)
from next.pages.signals import (
    context_registered,
    page_rendered,
    template_loaded,
)
from next.partial.signals import (
    field_validated,
    patch_op_registered,
    sse_stream_closed,
    sse_stream_opened,
    zone_registered,
    zone_rendered,
)
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    backend_loaded,
    collector_finalized,
    html_injected,
)
from next.urls.signals import route_registered, router_reloaded


__all__ = [
    "action_dispatched",
    "action_registered",
    "asset_registered",
    "backend_loaded",
    "collector_finalized",
    "component_backend_loaded",
    "component_registered",
    "component_rendered",
    "components_registered",
    "context_registered",
    "field_validated",
    "form_access_denied",
    "form_validation_failed",
    "html_injected",
    "page_rendered",
    "patch_op_registered",
    "provider_registered",
    "route_registered",
    "router_reloaded",
    "settings_reloaded",
    "sse_stream_closed",
    "sse_stream_opened",
    "template_loaded",
    "watch_specs_ready",
    "wizard_completed",
    "wizard_step_submitted",
    "zone_registered",
    "zone_rendered",
]
