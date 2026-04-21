"""Discover and render DJX components for templates.

Each subsystem lives in a small submodule. `info` holds the value
object, `loading` the module cache, `scanner` the filesystem walk,
`registry` the ordered store and visibility resolver, `context` the
`@component.context` decorator, `renderers` the render strategies,
`backends` the backend contract and factory, `manager` the orchestrator,
`watch` the read-only autoreload scan, and `facade` the short helpers
used from templates. Internal classes are reachable with deep imports
of the form `from next.components.registry import ComponentRegistry`.
"""

from __future__ import annotations

from next.conf import next_framework_settings

from . import checks, signals
from .backends import (
    BoomBackend,
    ComponentsBackend,
    ComponentsFactory,
    DummyBackend,
    FileComponentsBackend,
    register_components_folder_from_router_walk,
)
from .context import (
    ComponentContextManager,
    ComponentContextRegistry,
    ContextFunction,
    component,
    context,
)
from .facade import get_component, load_component_template, render_component
from .info import ComponentInfo
from .loading import ModuleCache, ModuleLoader
from .manager import ComponentsManager, components_manager
from .registry import ComponentRegistry, ComponentVisibilityResolver
from .renderers import (
    ComponentRenderer,
    ComponentRenderStrategy,
    ComponentTemplateLoader,
    CompositeComponentRenderer,
    SimpleComponentRenderer,
    _inject_component_context,
    _merge_csrf_context,
    _render_template_string,
)
from .scanner import ComponentScanner, component_extra_roots_from_config
from .watch import get_component_paths_for_watch


__all__ = [
    "BoomBackend",
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
    "ComponentsFactory",
    "ComponentsManager",
    "CompositeComponentRenderer",
    "ContextFunction",
    "DummyBackend",
    "FileComponentsBackend",
    "ModuleCache",
    "ModuleLoader",
    "SimpleComponentRenderer",
    "_inject_component_context",
    "_merge_csrf_context",
    "_render_template_string",
    "checks",
    "component",
    "component_extra_roots_from_config",
    "components_manager",
    "context",
    "get_component",
    "get_component_paths_for_watch",
    "load_component_template",
    "next_framework_settings",
    "register_components_folder_from_router_walk",
    "render_component",
    "signals",
]
