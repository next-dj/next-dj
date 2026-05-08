"""Discover and inject co-located static assets for pages and components.

Each `.djx` file may have matching `.css` and `.js` files (or any other
registered kind) in the same directory. Pages and components may also
declare URL list variables in their Python modules, named after a
registered placeholder slot. During rendering, a shared
`StaticCollector` gathers every referenced asset. After rendering,
`StaticManager.inject` replaces every registered placeholder token with
the rendered tags. Public URLs are resolved through Django staticfiles.

The `Next` JavaScript runtime is automatically injected on every page by
default. `StaticManager.inject` prepends `next.min.js` as the first
script and follows it with an inline init script that passes the
serialized JS context to `Next._init`. Context values opt into
JavaScript exposure by using `serialize=True` on their `@context`
decorator. A preload hint is injected immediately before `</head>` so
the browser downloads the file during HTML parsing. Users may switch to
`ScriptInjectionPolicy.DISABLED` or `ScriptInjectionPolicy.MANUAL` to
opt out.

The subsystem is fully type-agnostic. Built-in kinds such as `css` and
`js` are registered through the same public API exposed to user code,
so adding a new kind like `jsx` is a one-call extension.
"""

from __future__ import annotations

from . import signals
from .assets import KindRegistry, StaticAsset, default_kinds
from .backends import StaticBackend, StaticFilesBackend, StaticsFactory
from .collector import (
    PlaceholderRegistry,
    PlaceholderSlot,
    StaticCollector,
    default_placeholders,
)
from .defaults import register_defaults
from .discovery import AssetDiscovery
from .finders import NextStaticFilesFinder
from .manager import StaticManager, default_manager, reset_default_manager
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
    "StaticsFactory",
    "default_kinds",
    "default_manager",
    "default_placeholders",
    "register_defaults",
    "reset_default_manager",
    "resolve_serializer",
    "signals",
]
