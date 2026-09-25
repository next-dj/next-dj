"""Aggregate re-export of every signal emitted by the framework.

Import from here when one module subscribes to several subsystems at once.
"""

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
    form_backend_loaded,
    form_validation_failed,
    wizard_backend_loaded,
    wizard_completed,
    wizard_step_submitted,
)
from next.pages.signals import (
    context_registered,
    metadata_registered,
    page_rendered,
    template_loaded,
)
from next.partial.signals import (
    field_validated,
    partial_backend_loaded,
    patch_op_registered,
    sse_stream_closed,
    sse_stream_opened,
    zone_registered,
    zone_rendered,
)
from next.seo.signals import sitemap_items_registered
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    collector_finalized,
    html_injected,
    static_backend_loaded,
)
from next.urls.signals import route_registered, router_backend_loaded, router_reloaded


__all__ = [
    "action_dispatched",
    "action_registered",
    "asset_registered",
    "collector_finalized",
    "component_backend_loaded",
    "component_registered",
    "component_rendered",
    "components_registered",
    "context_registered",
    "field_validated",
    "form_access_denied",
    "form_backend_loaded",
    "form_validation_failed",
    "html_injected",
    "metadata_registered",
    "page_rendered",
    "partial_backend_loaded",
    "patch_op_registered",
    "provider_registered",
    "route_registered",
    "router_backend_loaded",
    "router_reloaded",
    "settings_reloaded",
    "sitemap_items_registered",
    "sse_stream_closed",
    "sse_stream_opened",
    "static_backend_loaded",
    "template_loaded",
    "watch_specs_ready",
    "wizard_backend_loaded",
    "wizard_completed",
    "wizard_step_submitted",
    "zone_registered",
    "zone_rendered",
]
