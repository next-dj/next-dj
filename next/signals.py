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
    form_validation_failed,
)
from next.pages.signals import (
    context_registered,
    page_rendered,
    template_loaded,
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
    "form_validation_failed",
    "html_injected",
    "page_rendered",
    "provider_registered",
    "route_registered",
    "router_reloaded",
    "settings_reloaded",
    "template_loaded",
    "watch_specs_ready",
]
