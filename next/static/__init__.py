"""Discover and inject co-located static assets for pages and components.

The `Next` runtime injects by default as the first script plus an inline init
script. `ScriptInjectionPolicy.DISABLED` or `MANUAL` opts out.
"""

from __future__ import annotations

from . import signals
from .assets import KindRegistry, StaticAsset, default_kinds
from .backends import StaticBackend, StaticFilesBackend
from .collector import (
    PlaceholderRegistry,
    PlaceholderSlot,
    StaticCollector,
    default_placeholders,
)
from .defaults import register_defaults
from .discovery import AssetDiscovery
from .finders import NextStaticFilesFinder
from .manager import (
    StaticManager,
    collect_component_assets,
    default_manager,
    get_static_manager,
    reset_default_manager,
)
from .scripts import NextScriptBuilder, ScriptInjectionPolicy
from .serializers import (
    JsContextSerializer,
    JsonJsContextSerializer,
    PydanticJsContextSerializer,
    resolve_serializer,
)


__all__ = [
    "AssetDiscovery",
    "JsContextSerializer",
    "JsonJsContextSerializer",
    "KindRegistry",
    "NextScriptBuilder",
    "NextStaticFilesFinder",
    "PlaceholderRegistry",
    "PlaceholderSlot",
    "PydanticJsContextSerializer",
    "ScriptInjectionPolicy",
    "StaticAsset",
    "StaticBackend",
    "StaticCollector",
    "StaticFilesBackend",
    "StaticManager",
    "collect_component_assets",
    "default_kinds",
    "default_manager",
    "default_placeholders",
    "get_static_manager",
    "register_defaults",
    "reset_default_manager",
    "resolve_serializer",
    "signals",
]
