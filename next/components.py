"""Component system for next-dj with backends, discovery, and scope-based resolution.

Provides simple (.djx) and composite (folder with component.djx / component.py)
components, configurable via NEXT_COMPONENTS. Components are resolved by name
and template path so that scope (parent/root visibility) is respected.
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from types import ModuleType

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template import Context as DjangoTemplateContext, Template

from .deps import DependencyCache, resolver


logger = logging.getLogger(__name__)

DEFAULT_COMPONENTS_DIR: Final[str] = "_components"
DEFAULT_PAGES_DIR: Final[str] = "pages"


class _ModuleCacheMissToken:
    """Sentinel: module path not present in LRU cache."""

    __slots__ = ()


_CACHE_MISS = _ModuleCacheMissToken()


class ModuleCache:
    """LRU cache for loaded Python modules with automatic eviction."""

    def __init__(self, maxsize: int = 128) -> None:
        """Initialize cache with maximum size."""
        self._cache: dict[Path, ModuleType | None] = {}
        self._maxsize = maxsize
        self._access_order: list[Path] = []

    def get(self, path: Path) -> ModuleType | None | _ModuleCacheMissToken:
        """Get module from cache or return _CACHE_MISS if not found."""
        if path in self._cache:
            # Update access order for LRU
            self._access_order.remove(path)
            self._access_order.append(path)
            return self._cache[path]
        return _CACHE_MISS

    def set(self, path: Path, module: ModuleType | None) -> None:
        """Store module in cache with LRU eviction."""
        # Evict oldest if at capacity
        if path not in self._cache and len(self._cache) >= self._maxsize:
            oldest = self._access_order.pop(0)
            del self._cache[oldest]

        self._cache[path] = module
        if path not in self._access_order:
            self._access_order.append(path)

    def clear(self) -> None:
        """Clear all cached modules."""
        self._cache.clear()
        self._access_order.clear()

    def __len__(self) -> int:
        """Return number of cached modules."""
        return len(self._cache)

    def __contains__(self, path: Path) -> bool:
        """Check if module is in cache."""
        return path in self._cache


class ModuleLoader:
    """Loads Python modules with LRU caching."""

    def __init__(self, cache: ModuleCache | None = None) -> None:
        """Initialize loader with optional cache."""
        self._cache = cache or ModuleCache()

    def load(self, path: Path) -> ModuleType | None:
        """Load module from path with caching."""
        cached = self._cache.get(path)
        if isinstance(cached, _ModuleCacheMissToken):
            module = self._load_from_disk(path)
            self._cache.set(path, module)
            return module
        return cached

    def _load_from_disk(self, path: Path) -> ModuleType | None:
        """Load module from disk without caching."""
        try:
            spec = importlib.util.spec_from_file_location(
                f"component_module_{path.stem}", path
            )
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except (ImportError, AttributeError, OSError, SyntaxError) as e:
            logger.debug("Could not load module %s: %s", path, e)
            return None
        else:
            return module


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    """Immutable info for a discovered component (simple or composite)."""

    name: str
    scope_root: Path
    scope_relative: str
    template_path: Path | None
    module_path: Path | None
    is_simple: bool

    def __str__(self) -> str:
        """Return string representation for logging."""
        comp_type = "simple" if self.is_simple else "composite"
        scope = self.scope_relative
        return f"ComponentInfo({self.name!r}, {comp_type}, scope={scope!r})"

    def __repr__(self) -> str:
        """Detailed representation for debugging."""
        return (
            f"ComponentInfo(name={self.name!r}, "
            f"scope_root={self.scope_root}, "
            f"scope_relative={self.scope_relative!r}, "
            f"is_simple={self.is_simple})"
        )

    def __hash__(self) -> int:
        """Hash for use in set/dict."""
        return hash((self.name, self.scope_root, self.scope_relative))

    def __eq__(self, other: object) -> bool:
        """Compare components for equality."""
        if not isinstance(other, ComponentInfo):
            return NotImplemented
        return (
            self.name == other.name
            and self.scope_root == other.scope_root
            and self.scope_relative == other.scope_relative
        )


def _load_python_module(file_path: Path) -> ModuleType | None:
    """Load Python module from file path, returning None on failure."""
    try:
        spec = importlib.util.spec_from_file_location("component_module", file_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, AttributeError, OSError, SyntaxError) as e:
        logger.debug("Could not load module %s: %s", file_path, e)
        return None
    else:
        return module


class ComponentRegistry:
    """Registry for discovered components."""

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._components: list[ComponentInfo] = []
        self._root_roots: set[Path] = set()

    def register(self, component: ComponentInfo) -> None:
        """Register a component in the registry."""
        self._components.append(component)

    def register_many(self, components: Iterable[ComponentInfo]) -> None:
        """Register multiple components at once."""
        self._components.extend(components)

    def get_all(self) -> Sequence[ComponentInfo]:
        """Get all registered components as immutable sequence."""
        return tuple(self._components)

    def mark_as_root(self, path: Path) -> None:
        """Mark a path as a root component directory."""
        self._root_roots.add(path)

    def is_root(self, path: Path) -> bool:
        """Check if path is marked as a root component directory."""
        return path in self._root_roots

    def clear(self) -> None:
        """Clear all registered components and root markers."""
        self._components.clear()
        self._root_roots.clear()

    def __len__(self) -> int:
        """Return number of registered components."""
        return len(self._components)

    def __contains__(self, name: str) -> bool:
        """Check if any component with given name exists."""
        return any(comp.name == name for comp in self._components)

    def __iter__(self) -> Iterator[ComponentInfo]:
        """Iterate over all registered components."""
        return iter(self._components)


class ComponentScanner:
    """Scans directories for component files and creates ComponentInfo instances."""

    def __init__(self, components_dir: str = DEFAULT_COMPONENTS_DIR) -> None:
        """Initialize scanner with component directory name."""
        self._components_dir = components_dir

    def scan_directory(
        self,
        directory: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> Sequence[ComponentInfo]:
        """Scan directory for simple .djx files and composite component folders."""
        components: list[ComponentInfo] = []

        try:
            for item in directory.iterdir():
                if item.is_file() and item.suffix == ".djx":
                    comp = self._create_simple_component(
                        item, scope_root, scope_relative
                    )
                    components.append(comp)
                elif item.is_dir():
                    composite = self._try_create_composite_component(
                        item, scope_root, scope_relative
                    )
                    if composite is not None:
                        components.append(composite)
        except OSError as e:
            logger.debug("Cannot scan directory %s: %s", directory, e)

        return components

    def _create_simple_component(
        self,
        djx_file: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> ComponentInfo:
        """Create ComponentInfo for a simple .djx component."""
        return ComponentInfo(
            name=djx_file.stem,
            scope_root=scope_root,
            scope_relative=scope_relative,
            template_path=djx_file,
            module_path=None,
            is_simple=True,
        )

    def _try_create_composite_component(
        self,
        directory: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> ComponentInfo | None:
        """Create ComponentInfo for composite component or None if not a component."""
        comp_djx = directory / "component.djx"
        comp_py = directory / "component.py"

        if not comp_djx.exists() and not comp_py.exists():
            return None

        template_path: Path | None = comp_djx if comp_djx.exists() else None

        if comp_py.exists() and template_path is None:
            mod = _module_loader.load(comp_py)
            if mod is not None and hasattr(mod, "component"):
                template_path = comp_py

        return ComponentInfo(
            name=directory.name,
            scope_root=scope_root,
            scope_relative=scope_relative,
            template_path=template_path,
            module_path=comp_py if comp_py.exists() else None,
            is_simple=False,
        )


class ComponentRootDiscovery:
    """Discovers root directories for components from Django apps and configuration."""

    def __init__(self, pages_dir: str = DEFAULT_PAGES_DIR) -> None:
        """Initialize discovery with pages directory name."""
        self._pages_dir = pages_dir

    def discover_app_roots(self) -> Sequence[Path]:
        """Discover pages directories from INSTALLED_APPS."""
        roots: list[Path] = []
        for app_name in self._get_installed_apps():
            pages_path = self._get_app_pages_path(app_name)
            if pages_path is not None:
                roots.append(pages_path.resolve())
        return roots

    def discover_component_roots(self, options: Mapping[str, Any]) -> Sequence[Path]:
        """Discover explicit component root directories from configuration."""
        roots: list[Path] = []

        # Check for COMPONENTS_DIRS (multiple directories)
        if "COMPONENTS_DIRS" in options:
            dirs = options["COMPONENTS_DIRS"]
            if isinstance(dirs, (list, tuple)):
                for item in dirs:
                    path = Path(item) if not isinstance(item, Path) else item
                    if path.exists():
                        roots.append(path.resolve())
            return roots

        # Check for COMPONENTS_DIR (single directory)
        if "COMPONENTS_DIR" in options:
            path = options["COMPONENTS_DIR"]
            path = Path(path) if not isinstance(path, Path) else path
            if path.exists():
                roots.append(path.resolve())

        return roots

    def _get_installed_apps(self) -> Iterable[str]:
        """Get non-Django INSTALLED_APPS."""
        for app in getattr(settings, "INSTALLED_APPS", []):
            if not app.startswith("django."):
                yield app

    def _get_app_pages_path(self, app_name: str) -> Path | None:
        """Get pages directory path for an app, or None if not found."""
        try:
            app_module = __import__(app_name, fromlist=[""])
            if app_module.__file__ is None:
                return None
            app_path = Path(app_module.__file__).parent
            pages_path = app_path / self._pages_dir
            return pages_path if pages_path.exists() else None
        except (ImportError, AttributeError):
            return None


class ComponentVisibilityResolver:
    """Resolves component visibility based on template path and scope rules."""

    def __init__(self, registry: ComponentRegistry) -> None:
        """Initialize resolver with component registry."""
        self._registry = registry
        self._path_cache: dict[tuple[Path, Path], list[str] | None] = {}

    def resolve_visible(self, template_path: Path) -> Mapping[str, ComponentInfo]:
        """Resolve components visible from template_path with scope-based priority."""
        template_path = template_path.resolve()

        # Collect candidates with visibility scores
        candidates: list[tuple[int, str, ComponentInfo]] = []

        for component in self._registry.get_all():
            score = self._calculate_visibility_score(component, template_path)
            if score is not None:
                candidates.append((score, component.name, component))

        # Sort by score (descending) then name (ascending)
        candidates.sort(key=lambda x: (-x[0], x[1]))

        # Deduplicate: first occurrence wins
        seen: set[str] = set()
        result: dict[str, ComponentInfo] = {}
        for _score, name, info in candidates:
            if name not in seen:
                result[name] = info
                seen.add(name)

        return result

    def _calculate_visibility_score(
        self, component: ComponentInfo, template_path: Path
    ) -> int | None:
        """Calculate visibility score. Returns None if not visible."""
        scope_root = component.scope_root
        scope_rel = component.scope_relative or ""

        # Root components are always visible
        if self._registry.is_root(scope_root) and not scope_rel:
            return 0

        # Check if component scope is visible from template path
        parts = self._get_relative_parts_cached(template_path, scope_root)
        if parts is None or scope_rel not in parts:
            return None

        return len(scope_rel) if scope_rel else 0

    def _get_relative_parts_cached(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        """Get relative path parts with caching."""
        cache_key = (template_path, scope_root)

        if cache_key not in self._path_cache:
            self._path_cache[cache_key] = self._compute_relative_parts(
                template_path, scope_root
            )

        return self._path_cache[cache_key]

    def _compute_relative_parts(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        """Compute relative path parts from template to scope root."""
        try:
            template_dir = template_path.parent
            rel = template_dir.relative_to(scope_root)
            parts = rel.parts
            return ["/".join(parts[:i]) if i else "" for i in range(len(parts), -1, -1)]
        except ValueError:
            return None

    def clear_cache(self) -> None:
        """Clear the path resolution cache."""
        self._path_cache.clear()


class ComponentTemplateLoader:
    """Loads component templates from .djx files or module attributes."""

    def __init__(self, module_loader: ModuleLoader) -> None:
        """Initialize loader with module loader for composite components."""
        self._module_loader = module_loader

    def load(self, info: ComponentInfo) -> str | None:
        """Load template string for component."""
        # Try loading from .djx file
        if info.template_path is not None and info.template_path.suffix == ".djx":
            with contextlib.suppress(OSError, UnicodeDecodeError):
                return info.template_path.read_text(encoding="utf-8")

        # Try loading from module attribute
        if info.module_path is not None:
            module = self._module_loader.load(info.module_path)
            if module is not None and hasattr(module, "component"):
                return getattr(module, "component", None)

        return None


class ComponentRenderStrategy(Protocol):
    """Protocol for component rendering strategies."""

    def can_render(self, info: ComponentInfo) -> bool:
        """Check if this strategy can render the given component."""
        ...

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render the component and return HTML string."""
        ...


class SimpleComponentRenderer:
    """Renders simple .djx components without Python logic."""

    def __init__(self, template_loader: ComponentTemplateLoader) -> None:
        """Initialize renderer with template loader."""
        self._loader = template_loader

    def can_render(self, info: ComponentInfo) -> bool:
        """Can render simple components or composites without module."""
        return info.is_simple or info.module_path is None

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render simple component by loading template and rendering with context."""
        del request
        template_str = self._loader.load(info)
        if template_str is None:
            return ""

        return Template(template_str).render(DjangoTemplateContext(dict(context_data)))


class CompositeComponentRenderer:
    """Renders composite components with component.py logic."""

    def __init__(
        self,
        module_loader: ModuleLoader,
        template_loader: ComponentTemplateLoader,
    ) -> None:
        """Initialize renderer with loaders."""
        self._module_loader = module_loader
        self._template_loader = template_loader

    def can_render(self, info: ComponentInfo) -> bool:
        """Can render composite components with module."""
        return not info.is_simple and info.module_path is not None

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render composite component using module logic or template."""
        if info.module_path is None:
            return ""

        module = self._module_loader.load(info.module_path)
        if module is None:
            return self._fallback_to_template(info, context_data)

        render_func = getattr(module, "render", None)
        if callable(render_func):
            return self._render_with_function(render_func, context_data, request)

        return self._render_with_template(info, context_data, request)

    def _render_with_function(
        self,
        render_func: Callable[..., Any],
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render using custom render() function with DI."""
        cache = DependencyCache()
        stack: list[str] = []

        resolved = resolver.resolve_with_template_context(
            render_func,
            request=request,
            template_context=dict(context_data),
            _cache=cache,
            _stack=stack,
        )

        result = render_func(**resolved)

        if isinstance(result, HttpResponse):
            return result.content.decode()
        return str(result)

    def _render_with_template(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render using template with context injection."""
        template_str = self._template_loader.load(info)
        if template_str is None:
            return ""

        context_dict = dict(context_data)
        if request is not None:
            context_dict["request"] = request

        _inject_component_context(info, context_dict, request)

        return Template(template_str).render(DjangoTemplateContext(context_dict))

    def _fallback_to_template(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
    ) -> str:
        """Fallback to template rendering when module fails to load."""
        template_str = self._template_loader.load(info)
        if template_str is None:
            return ""
        return Template(template_str).render(DjangoTemplateContext(dict(context_data)))


class ComponentRenderer:
    """Coordinates component rendering using appropriate strategy."""

    def __init__(self, strategies: Sequence[ComponentRenderStrategy]) -> None:
        """Initialize renderer with rendering strategies."""
        self._strategies = strategies

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None = None,
    ) -> str:
        """Render component using the first applicable strategy."""
        for strategy in self._strategies:
            if strategy.can_render(info):
                return strategy.render(info, context_data, request)

        return ""


class ComponentsBackend(ABC):
    """Abstract base for component discovery and resolution."""

    @abstractmethod
    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Return component info for name visible from template_path, or None."""

    @abstractmethod
    def collect_visible_components(
        self,
        template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Return mapping of name to ComponentInfo for components visible from path."""


class FileComponentsBackend(ComponentsBackend):
    """File-based component backend using composition."""

    def __init__(
        self,
        components_dir: str = DEFAULT_COMPONENTS_DIR,
        *,
        app_dirs: bool = True,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Initialize backend with configuration."""
        self.components_dir = components_dir
        self.app_dirs = app_dirs
        self.options = options or {}

        # Create internal components using composition
        self._registry = ComponentRegistry()
        self._scanner = ComponentScanner(components_dir)
        pages_dir = str(self.options.get("PAGES_DIR", DEFAULT_PAGES_DIR))
        self._root_discovery = ComponentRootDiscovery(pages_dir)
        self._visibility_resolver = ComponentVisibilityResolver(self._registry)

        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Ensure components are discovered and registered."""
        if self._loaded:
            return
        self._discover_and_register_all()
        self._loaded = True

    def _discover_and_register_all(self) -> None:
        """Discover all components and register them in the registry."""
        # Discover from app pages directories
        if self.app_dirs:
            for pages_root in self._root_discovery.discover_app_roots():
                self._discover_in_pages_root(pages_root)

        # Discover from explicit component roots
        for comp_root in self._root_discovery.discover_component_roots(self.options):
            self._registry.mark_as_root(comp_root)
            self._discover_in_component_root(comp_root)

    def _discover_in_pages_root(self, pages_root: Path) -> None:
        """Discover components in a pages root by finding _components directories."""
        try:
            for path in pages_root.rglob("*"):
                if not path.is_dir() or path.name != self.components_dir:
                    continue

                parent = path.parent
                try:
                    scope_relative = parent.relative_to(pages_root).as_posix()
                    if scope_relative == ".":
                        scope_relative = ""
                except ValueError:
                    scope_relative = ""

                components = self._scanner.scan_directory(
                    path, pages_root, scope_relative
                )
                self._registry.register_many(components)
        except OSError as e:
            logger.debug("Cannot discover components under %s: %s", pages_root, e)

    def _discover_in_component_root(self, component_root: Path) -> None:
        """Discover components in a root component directory."""
        components = self._scanner.scan_directory(component_root, component_root, "")
        self._registry.register_many(components)

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Return component info for name visible from template_path, or None."""
        self._ensure_loaded()
        visible = self.collect_visible_components(template_path)
        return visible.get(name)

    def collect_visible_components(
        self,
        template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Return name to ComponentInfo for all visible from template_path."""
        self._ensure_loaded()
        return self._visibility_resolver.resolve_visible(template_path)


class ComponentsFactory:
    """Factory for creating component backend instances from configuration."""

    _backends: ClassVar[dict[str, type[ComponentsBackend]]] = {
        "next.components.FileComponentsBackend": FileComponentsBackend,
    }

    @classmethod
    def register_backend(
        cls, name: str, backend_class: type[ComponentsBackend]
    ) -> None:
        """Register a backend type for runtime extensibility."""
        cls._backends[name] = backend_class

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> ComponentsBackend:
        """Create a backend instance from a NEXT_COMPONENTS config entry."""
        backend_name = config.get("BACKEND", "next.components.FileComponentsBackend")
        if backend_name not in cls._backends:
            msg = f"Unsupported backend: {backend_name}"
            raise ValueError(msg)
        backend_class = cls._backends[backend_name]
        if issubclass(backend_class, FileComponentsBackend):
            return backend_class(
                components_dir=config.get("OPTIONS", {}).get(
                    "COMPONENTS_DIR",
                    DEFAULT_COMPONENTS_DIR,
                ),
                app_dirs=config.get("APP_DIRS", True),
                options=config.get("OPTIONS", {}),
            )
        return backend_class()


class ComponentsManager:
    """Central manager for component backends. Resolves by name and template path."""

    def __init__(self) -> None:
        """Create empty manager; backends loaded from NEXT_COMPONENTS on first use."""
        self._backends: list[ComponentsBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    def _reload_config(self) -> None:
        self._config_cache = None
        self._backends.clear()
        configs = getattr(settings, "NEXT_COMPONENTS", None)
        if not isinstance(configs, list):
            return
        for config in configs:
            if not isinstance(config, dict):
                continue
            try:
                backend = ComponentsFactory.create_backend(config)
                self._backends.append(backend)
            except Exception:
                logger.exception(
                    "Error creating component backend from config %s", config
                )

    def _ensure_backends(self) -> None:
        if not self._backends:
            self._reload_config()

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Return first component with given name visible from template_path."""
        self._ensure_backends()
        for backend in self._backends:
            info = backend.get_component(name, template_path)
            if info is not None:
                return info
        return None

    def collect_visible_components(
        self, template_path: Path
    ) -> Mapping[str, ComponentInfo]:
        """Merge visible components from all backends. First backend wins per name."""
        self._ensure_backends()
        merged: dict[str, ComponentInfo] = {}
        for backend in self._backends:
            for name, info in backend.collect_visible_components(template_path).items():
                if name not in merged:
                    merged[name] = info
        return merged


components_manager = ComponentsManager()


def get_component(name: str, template_path: Path) -> ComponentInfo | None:
    """Return component info for name visible from template_path."""
    return components_manager.get_component(name, template_path)


def load_component_template(info: ComponentInfo) -> str | None:
    """Load template string for a component."""
    return _template_loader.load(info)


# Global instances for rendering
_module_loader = ModuleLoader()
_template_loader = ComponentTemplateLoader(_module_loader)
_simple_renderer = SimpleComponentRenderer(_template_loader)
_composite_renderer = CompositeComponentRenderer(_module_loader, _template_loader)
_component_renderer = ComponentRenderer([_composite_renderer, _simple_renderer])


def render_component(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None = None,
) -> str:
    """Render a component for the given context using the global renderer."""
    return _component_renderer.render(info, context_data, request)


def _inject_component_context(
    info: ComponentInfo,
    context_data: dict[str, Any],
    request: HttpRequest | None,
) -> None:
    """Run component context functions from component.py and merge into context_data."""
    if info.module_path is None:
        return

    ctx_funcs = component.get_functions(info.module_path)
    if not ctx_funcs:
        return

    cache = DependencyCache()
    stack: list[str] = []

    for ctx_func in ctx_funcs:
        resolved = resolver.resolve_with_template_context(
            ctx_func.func,
            request=request,
            template_context=context_data,
            _cache=cache,
            _stack=stack,
        )

        if ctx_func.key is None:
            data = ctx_func.func(**resolved)
            if isinstance(data, dict):
                context_data.update(data)
        else:
            context_data[ctx_func.key] = ctx_func.func(**resolved)


@dataclass(frozen=True, slots=True)
class ContextFunction:
    """Represents a registered context function for a component."""

    func: Callable[..., Any]
    key: str | None


class ComponentContextRegistry:
    """Registry for component context functions without frame inspection magic.

    Stores context functions that provide data to component templates.
    Functions can be keyed (added to context as specific key) or unkeyed
    (merged into context as dict).
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._registry: dict[Path, dict[str | None, ContextFunction]] = {}

    def register(
        self,
        component_path: Path,
        key: str | None,
        func: Callable[..., Any],
    ) -> None:
        """Register a context function for a component.

        Validates that key is not reserved and checks for duplicate registrations.
        Allows re-registration of the same function (e.g., during module reload).
        """
        path = component_path.resolve()

        # Validate key is not reserved
        if isinstance(key, str) and key in resolver.EXPLICIT_RESOLVE_KEYS:
            msg = (
                f"Component context key {key!r} is reserved for dependency injection; "
                f"use another name. Reserved: {sorted(resolver.EXPLICIT_RESOLVE_KEYS)}."
            )
            raise ValueError(msg)

        # Get or create component registry
        component_registry = self._registry.setdefault(path, {})

        # Check for duplicate registration
        if key in component_registry:
            existing = component_registry[key]
            if not self._is_same_function(existing.func, func):
                if key is None:
                    dup_desc = "unkeyed @component.context"
                else:
                    dup_desc = f"key {key!r}"
                msg = (
                    f"Duplicate component context registration ({dup_desc}) for {path}"
                )
                raise ValueError(msg)

        component_registry[key] = ContextFunction(func=func, key=key)

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Get all context functions for a component."""
        path = component_path.resolve()
        registry = self._registry.get(path, {})
        return tuple(registry.values())

    def _is_same_function(
        self, func1: Callable[..., Any], func2: Callable[..., Any]
    ) -> bool:
        """Check if two functions are the same."""
        if func1 is func2:
            return True

        name1 = getattr(func1, "__name__", None)
        name2 = getattr(func2, "__name__", None)
        if not name1 or not name2 or name1 != name2:
            return False

        try:
            file1 = inspect.getsourcefile(func1)
            file2 = inspect.getsourcefile(func2)
            if not file1 or not file2:
                return False
            return Path(file1).resolve() == Path(file2).resolve()
        except (OSError, TypeError, ValueError):
            return False

    def __len__(self) -> int:
        """Return total number of registered context functions."""
        return sum(len(funcs) for funcs in self._registry.values())


class ComponentContextManager:
    """Manager for component context functions."""

    def __init__(self) -> None:
        """Initialize the context manager."""
        self._registry = ComponentContextRegistry()

    def _get_caller_path(self, back_count: int = 1) -> Path:
        """Extract the file path of the calling code using stack frame inspection."""
        frame = inspect.currentframe()
        for _ in range(back_count):
            if not frame or not frame.f_back:
                msg = "Could not determine caller file path"
                raise RuntimeError(msg)
            frame = frame.f_back

        for _ in range(10):
            if not frame:
                break
            file_path = frame.f_globals.get("__file__")
            if file_path and not file_path.endswith("components.py"):
                return Path(file_path)
            frame = frame.f_back

        msg = "Could not determine caller file path"
        raise RuntimeError(msg)

    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register context functions that provide template variables.

        ```py
        @component.context("user")
        def get_user(request: HttpRequest) -> User:
            return request.user


        @component.context
        def get_data(request: HttpRequest) -> dict:
            return {"count": 42}
        ```
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                caller_path = self._get_caller_path(2)
                self._registry.register(caller_path, None, func_or_key)
            else:
                caller_path = self._get_caller_path(1)
                self._registry.register(caller_path, func_or_key, func)
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Get all registered context functions for a component."""
        return self._registry.get_functions(component_path)


# global singleton instance for component context management
component = ComponentContextManager()

# convenience alias for the context decorator
context = component.context
