"""Discover and inject co-located static assets for pages and components.

Each `.djx` may have sibling files of any registered kind, and a module may declare URL
lists named after a placeholder slot. A shared `StaticCollector` gathers them during
render and `StaticManager.inject` swaps each placeholder token for the rendered tags.

The `Next` runtime is injected by default as the first script plus an inline init
script, and `ScriptInjectionPolicy.DISABLED` or `MANUAL` opts out.

Built-in kinds register through the same public API user code uses.
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
