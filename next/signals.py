"""Aggregate re-export of every signal emitted by the framework.

Import from here when one module subscribes to several subsystems at once.
"""

from next.boundaries import boundary_failed
from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)
from next.compression.signals import compression_backend_loaded
from next.conf.signals import settings_reloaded
from next.consent.signals import consent_backend_loaded
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
    intercept_access_denied,
    partial_backend_loaded,
    patch_op_registered,
    sse_stream_closed,
    sse_stream_opened,
    zone_registered,
    zone_rendered,
)
from next.realtime.signals import (
    broker_backend_loaded,
    message_published,
    stream_subscribed,
    stream_unsubscribed,
    upstream_received,
    upstream_rejected,
)
from next.router.signals import navigation_rendered, prefetch_shed, router_cleared
from next.scripts.signals import scripts_registered
from next.seo.signals import sitemap_backend_loaded
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    collector_finalized,
    html_injected,
    static_backend_loaded,
)
from next.suspense.signals import (
    boundary_rendered,
    boundary_side_effect,
    boundary_token_rejected,
    page_stream_closed,
    page_stream_declined,
    page_stream_opened,
)
from next.urls.signals import (
    intercept_registered,
    route_registered,
    router_backend_loaded,
    router_reloaded,
)


__all__ = [
    "action_dispatched",
    "action_registered",
    "asset_registered",
    "boundary_failed",
    "boundary_rendered",
    "boundary_side_effect",
    "boundary_token_rejected",
    "broker_backend_loaded",
    "collector_finalized",
    "component_backend_loaded",
    "component_registered",
    "component_rendered",
    "components_registered",
    "compression_backend_loaded",
    "consent_backend_loaded",
    "context_registered",
    "field_validated",
    "form_access_denied",
    "form_backend_loaded",
    "form_validation_failed",
    "html_injected",
    "intercept_access_denied",
    "intercept_registered",
    "message_published",
    "metadata_registered",
    "navigation_rendered",
    "page_rendered",
    "page_stream_closed",
    "page_stream_declined",
    "page_stream_opened",
    "partial_backend_loaded",
    "patch_op_registered",
    "prefetch_shed",
    "provider_registered",
    "route_registered",
    "router_backend_loaded",
    "router_cleared",
    "router_reloaded",
    "scripts_registered",
    "settings_reloaded",
    "sitemap_backend_loaded",
    "sse_stream_closed",
    "sse_stream_opened",
    "static_backend_loaded",
    "stream_subscribed",
    "stream_unsubscribed",
    "template_loaded",
    "upstream_received",
    "upstream_rejected",
    "watch_specs_ready",
    "wizard_backend_loaded",
    "wizard_completed",
    "wizard_step_submitted",
    "zone_registered",
    "zone_rendered",
]
