"""Aggregate re-export of every signal emitted by the framework.

Importing the sub-package signal module directly is still supported.
This module provides a single import path for code that wants to wire
multiple signals without remembering which subsystem owns each one.
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
