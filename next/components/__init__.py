"""Discover and render DJX components for templates.

Each subsystem lives in its own submodule, and internal classes stay reachable through
deep imports such as `from next.components.registry import ComponentRegistry`.
"""

from __future__ import annotations

from . import checks, signals
from .backends import ComponentsBackend, FileComponentsBackend
from .context import (
    ComponentContextManager,
    ComponentContextRegistry,
    ContextFunction,
    component,
    context,
)
from .facade import (
    collect_visible_components,
    get_component,
    load_component_template,
    render_component,
)
from .info import ComponentInfo
from .loading import ModuleCache, ModuleLoader
from .manager import (
    ComponentsManager,
    components_manager,
    register_components_folder_from_router_walk,
)
from .registry import ComponentRegistry, ComponentVisibilityResolver
from .renderers import (
    CachedComponentTemplateLoader,
    ComponentRenderer,
    ComponentRenderStrategy,
    ComponentTemplateLoader,
    CompositeComponentRenderer,
    SimpleComponentRenderer,
)
from .scanner import ComponentScanner, component_extra_roots_from_config
from .watch import component_watch_roots, get_component_paths_for_watch


__all__ = [
    "CachedComponentTemplateLoader",
    "ComponentContextManager",
    "ComponentContextRegistry",
    "ComponentInfo",
    "ComponentRegistry",
    "ComponentRenderStrategy",
    "ComponentRenderer",
    "ComponentScanner",
    "ComponentTemplateLoader",
    "ComponentVisibilityResolver",
    "ComponentsBackend",
    "ComponentsManager",
    "CompositeComponentRenderer",
    "ContextFunction",
    "FileComponentsBackend",
    "ModuleCache",
    "ModuleLoader",
    "SimpleComponentRenderer",
    "checks",
    "collect_visible_components",
    "component",
    "component_extra_roots_from_config",
    "component_watch_roots",
    "components_manager",
    "context",
    "get_component",
    "get_component_paths_for_watch",
    "load_component_template",
    "register_components_folder_from_router_walk",
    "render_component",
    "signals",
]
