"""Discover and render DJX components for templates."""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import itertools
import logging
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.template import Context as DjangoTemplateContext, Template
from django.utils.functional import SimpleLazyObject

from .conf import import_class_cached, next_framework_settings
from .deps import DependencyCache, resolver
from .utils import caller_source_path, classify_dirs_entries, resolve_base_dir


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from types import ModuleType

logger = logging.getLogger(__name__)

__all__ = [
    "ComponentContextManager",
    "ComponentInfo",
    "ComponentsBackend",
    "ComponentsFactory",
    "ComponentsManager",
    "ContextFunction",
    "FileComponentsBackend",
    "component",
    "component_extra_roots_from_config",
    "components_manager",
    "context",
    "get_component",
    "get_component_paths_for_watch",
    "load_component_template",
    "render_component",
]

_CACHE_MISS = object()


class ModuleCache:
    """Remembers loaded Python modules by file path and drops the oldest when full."""

    def __init__(self, maxsize: int = 128) -> None:
        self._maxsize = maxsize
        self._order: OrderedDict[Path, ModuleType | None] = OrderedDict()

    def get(self, path: Path) -> ModuleType | None | object:
        if path not in self._order:
            return _CACHE_MISS
        self._order.move_to_end(path)
        return self._order[path]

    def set(self, path: Path, module: ModuleType | None) -> None:
        if path not in self._order and len(self._order) >= self._maxsize:
            self._order.popitem(last=False)
        self._order[path] = module
        self._order.move_to_end(path)

    def clear(self) -> None:
        self._order.clear()

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, path: Path) -> bool:
        return path in self._order


class ModuleLoader:
    """Loads a ``.py`` file as a module and reuses the last load for the same path."""

    def __init__(self, cache: ModuleCache | None = None) -> None:
        self._cache = cache or ModuleCache()

    def load(self, path: Path) -> ModuleType | None:
        cached = self._cache.get(path)
        if cached is _CACHE_MISS:
            module = self._load_from_disk(path)
            self._cache.set(path, module)
            return module
        return cast("ModuleType | None", cached)

    def _load_from_disk(self, path: Path) -> ModuleType | None:
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
    """What we know about one component after scanning the filesystem."""

    name: str
    scope_root: Path
    scope_relative: str
    template_path: Path | None
    module_path: Path | None
    is_simple: bool

    @property
    def scope_key(self) -> tuple[str, Path, str]:
        """Stable tuple for grouping by name and scope (ignores template paths)."""
        return (self.name, self.scope_root, self.scope_relative)


class ComponentRegistry:
    """Holds discovered components and whether a directory is a global root."""

    def __init__(self) -> None:
        self._ordered: list[ComponentInfo] = []
        self._by_name: dict[str, list[ComponentInfo]] = {}
        self._root_roots: set[Path] = set()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    def _bump(self) -> None:
        self._version += 1

    def register(self, component: ComponentInfo) -> None:
        self._ordered.append(component)
        self._by_name.setdefault(component.name, []).append(component)
        self._bump()

    def register_many(self, components: Iterable[ComponentInfo]) -> None:
        for c in components:
            self.register(c)

    def get_all(self) -> Sequence[ComponentInfo]:
        return tuple(self._ordered)

    def mark_as_root(self, path: Path) -> None:
        self._root_roots.add(path)

    def is_root(self, path: Path) -> bool:
        return path in self._root_roots

    def clear(self) -> None:
        self._ordered.clear()
        self._by_name.clear()
        self._root_roots.clear()
        self._bump()

    def __len__(self) -> int:
        return len(self._ordered)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[ComponentInfo]:
        return iter(self._ordered)


class ComponentScanner:
    """Scan one folder for ``.djx`` files and composite component directories."""

    DEFAULT_COMPONENTS_DIR_NAME: str = "_components"

    def __init__(
        self,
        components_dir: str | None = None,
        *,
        module_loader: ModuleLoader | None = None,
    ) -> None:
        self._components_dir = (
            components_dir
            if components_dir is not None
            else self.DEFAULT_COMPONENTS_DIR_NAME
        )
        self._module_loader = module_loader or ModuleLoader()

    def scan_directory(
        self,
        directory: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> Sequence[ComponentInfo]:
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
        comp_djx = directory / "component.djx"
        comp_py = directory / "component.py"

        if not comp_djx.exists() and not comp_py.exists():
            return None

        template_path: Path | None = comp_djx if comp_djx.exists() else None

        if comp_py.exists() and template_path is None:
            mod = self._module_loader.load(comp_py)
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


def component_extra_roots_from_config(config: Mapping[str, Any]) -> list[Path]:
    """Return existing directory paths from the config DIRS entry."""
    base_dir = resolve_base_dir()
    dirs_list = list(config.get("DIRS") or [])
    path_roots, _ = classify_dirs_entries(dirs_list, base_dir)
    return [p for p in path_roots if p.exists()]


def _paths_from_component_info(info: ComponentInfo) -> set[Path]:
    """Return resolved filesystem paths that define one component."""
    out: set[Path] = set()
    if info.template_path is not None:
        with contextlib.suppress(OSError):
            out.add(info.template_path.resolve())
    if info.module_path is not None:
        with contextlib.suppress(OSError):
            out.add(info.module_path.resolve())
    return out


def _collect_paths_for_one_pages_root(
    scanner: ComponentScanner,
    comp_name: str,
    root: Path,
) -> set[Path]:
    """Gather component paths under one pages tree root."""
    result: set[Path] = set()
    try:
        for path in root.glob(f"**/{comp_name}"):
            if not path.is_dir():
                continue
            try:
                rel_parent = path.parent.relative_to(root)
            except ValueError:
                continue
            scope_relative = "/".join(rel_parent.parts) if rel_parent.parts else ""
            for info in scanner.scan_directory(path, root, scope_relative):
                result |= _paths_from_component_info(info)
    except OSError as e:
        logger.debug(
            "Cannot scan %s for component dirs %s: %s",
            root,
            comp_name,
            e,
        )
    return result


def _collect_component_paths_under_page_trees() -> set[Path]:
    """Collect component paths from page backends without mutating registries."""
    from next.urls import RouterFactory  # noqa: PLC0415

    result: set[Path] = set()
    page_configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(page_configs, list):
        return result
    for config in page_configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating page backend for component autoreload scan %s",
                config,
            )
            continue
        if not RouterFactory.is_filesystem_discovery_router(backend):
            continue
        fs_backend: Any = backend
        comp_name = str(fs_backend._components_folder_name)
        scanner = ComponentScanner(comp_name)
        for root in itertools.chain(
            (p.resolve() for p in fs_backend._get_root_pages_paths()),
            (
                a.resolve()
                for app_name in fs_backend._get_installed_apps()
                if (a := fs_backend._get_app_pages_path(app_name))
            ),
        ):
            result |= _collect_paths_for_one_pages_root(scanner, comp_name, root)
    return result


def _collect_component_paths_from_backend_dirs() -> set[Path]:
    """Collect paths from component backend DIRS entries only."""
    result: set[Path] = set()
    comp_configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(comp_configs, list):
        return result
    for config in comp_configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = ComponentsFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating component backend for autoreload scan %s", config
            )
            continue
        if not isinstance(backend, FileComponentsBackend):
            continue
        scanner = ComponentScanner(
            backend.components_dir,
            module_loader=ModuleLoader(),
        )
        for root in component_extra_roots_from_config(config):
            try:
                for info in scanner.scan_directory(root, root, ""):
                    result |= _paths_from_component_info(info)
            except OSError as e:
                logger.debug(
                    "Cannot scan component root %s: %s",
                    root,
                    e,
                )
    return result


def get_component_paths_for_watch() -> set[Path]:
    """Return filesystem paths that matter for the development component reloader.

    This performs a read-only scan. It does not mutate the components manager or
    router registration state.
    """
    page_paths = _collect_component_paths_under_page_trees()
    extra_paths = _collect_component_paths_from_backend_dirs()
    return page_paths | extra_paths


class ComponentVisibilityResolver:
    """Decides which component names exist for a given template file path."""

    def __init__(self, registry: ComponentRegistry) -> None:
        self._registry = registry
        self._path_cache: dict[tuple[Path, Path], list[str] | None] = {}
        self._result_cache: dict[Path, Mapping[str, ComponentInfo]] = {}
        self._scope_index: dict[Path, list[ComponentInfo]] = {}
        self._scope_index_registry_version = -1
        self._cached_registry_version = -1

    def _ensure_scope_index(self) -> None:
        if self._scope_index_registry_version == self._registry.version:
            return
        self._scope_index = {}
        for ci in self._registry.get_all():
            root = ci.scope_root.resolve()
            self._scope_index.setdefault(root, []).append(ci)
        self._scope_index_registry_version = self._registry.version

    def _candidate_components(self, template_path: Path) -> list[ComponentInfo]:
        self._ensure_scope_index()
        tmpl_dir = template_path.parent
        out: list[ComponentInfo] = []
        for scope_root, infos in self._scope_index.items():
            if self._registry.is_root(scope_root):
                out.extend(infos)
                continue
            try:
                tmpl_dir.relative_to(scope_root)
            except ValueError:
                continue
            else:
                out.extend(infos)
        return out

    def resolve_visible(self, template_path: Path) -> Mapping[str, ComponentInfo]:
        template_path = template_path.resolve()

        if self._cached_registry_version != self._registry.version:
            self._result_cache.clear()
            self._path_cache.clear()
            self._scope_index_registry_version = -1
            self._cached_registry_version = self._registry.version

        if template_path in self._result_cache:
            return self._result_cache[template_path]

        candidates: list[tuple[int, str, ComponentInfo]] = []

        for component in self._candidate_components(template_path):
            score = self._calculate_visibility_score(component, template_path)
            if score is not None:
                candidates.append((score, component.name, component))

        candidates.sort(key=lambda x: (-x[0], x[1]))

        seen: set[str] = set()
        result: dict[str, ComponentInfo] = {}
        for _score, name, info in candidates:
            if name not in seen:
                result[name] = info
                seen.add(name)

        self._result_cache[template_path] = result
        return result

    def _calculate_visibility_score(
        self, component: ComponentInfo, template_path: Path
    ) -> int | None:
        scope_root = component.scope_root
        scope_rel = component.scope_relative or ""

        if self._registry.is_root(scope_root) and not scope_rel:
            return 0

        parts = self._get_relative_parts_cached(template_path, scope_root)
        if parts is None or scope_rel not in parts:
            return None

        return len(scope_rel) if scope_rel else 0

    def _get_relative_parts_cached(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        cache_key = (template_path, scope_root)

        if cache_key not in self._path_cache:
            self._path_cache[cache_key] = self._compute_relative_parts(
                template_path, scope_root
            )

        return self._path_cache[cache_key]

    def _compute_relative_parts(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        try:
            template_dir = template_path.parent
            rel = template_dir.relative_to(scope_root)
            parts = rel.parts
            if not parts:
                return [""]
            return ["/".join(parts[:i]) if i else "" for i in range(len(parts), -1, -1)]
        except ValueError:
            return None

    def clear_cache(self) -> None:
        self._path_cache.clear()
        self._result_cache.clear()
        self._scope_index_registry_version = -1


class ComponentTemplateLoader:
    """Read template source from a ``.djx`` file or a ``component`` module string."""

    def __init__(self, module_loader: ModuleLoader) -> None:
        self._module_loader = module_loader

    def load(self, info: ComponentInfo) -> str | None:
        if info.template_path is not None and info.template_path.suffix == ".djx":
            with contextlib.suppress(OSError, UnicodeDecodeError):
                return info.template_path.read_text(encoding="utf-8")

        if info.module_path is not None:
            module = self._module_loader.load(info.module_path)
            if module is not None and hasattr(module, "component"):
                return getattr(module, "component", None)

        return None


@dataclass(frozen=True, slots=True)
class ContextFunction:
    """One function registered to add variables before a component template runs."""

    func: Callable[..., Any]
    key: str | None


class ComponentContextRegistry:
    """Maps ``component.py`` paths to functions that supply template variables."""

    def __init__(self) -> None:
        self._registry: dict[Path, dict[str | None, ContextFunction]] = {}

    def register(
        self,
        component_path: Path,
        key: str | None,
        func: Callable[..., Any],
    ) -> None:
        path = component_path.resolve()

        if isinstance(key, str) and key in resolver.EXPLICIT_RESOLVE_KEYS:
            msg = (
                f"Component context key {key!r} is reserved for dependency injection. "
                f"Use another name. Reserved: {sorted(resolver.EXPLICIT_RESOLVE_KEYS)}."
            )
            raise ValueError(msg)

        component_registry = self._registry.setdefault(path, {})

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
        path = component_path.resolve()
        registry = self._registry.get(path, {})
        return tuple(registry.values())

    def _is_same_function(
        self, func1: Callable[..., Any], func2: Callable[..., Any]
    ) -> bool:
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
        return sum(len(funcs) for funcs in self._registry.values())


class ComponentContextManager:
    """Registers and looks up context helpers used from ``component.py``."""

    def __init__(self) -> None:
        """Create an empty registry for context callables."""
        self._registry = ComponentContextRegistry()

    def _get_caller_path(self, back_count: int = 1) -> Path:
        return caller_source_path(
            back_count=back_count,
            max_walk=10,
            skip_framework_file=("components.py", "next"),
        )

    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Mark a function so it fills template variables for this component module."""

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
        """Return context callables registered for this ``component.py`` path."""
        return self._registry.get_functions(component_path)


component = ComponentContextManager()
context = component.context


def _render_template_string(template_str: str, context_dict: dict[str, Any]) -> str:
    return Template(template_str).render(DjangoTemplateContext(context_dict))


def _merge_csrf_context(
    context_dict: dict[str, Any],
    request: HttpRequest | None,
) -> None:
    """Lazy ``csrf_token`` for plain ``Context`` (matches request-context processor)."""
    if request is None or "csrf_token" in context_dict:
        return

    context_dict["csrf_token"] = SimpleLazyObject(lambda: get_token(request))


def _inject_component_context(
    info: ComponentInfo,
    context_data: dict[str, Any],
    request: HttpRequest | None,
) -> None:
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


class ComponentRenderStrategy(Protocol):
    """Optional render path for a ``ComponentInfo``."""

    def can_render(self, info: ComponentInfo) -> bool:
        raise NotImplementedError

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        raise NotImplementedError


class SimpleComponentRenderer:
    """Uses the template string only (no ``component.py``)."""

    def __init__(self, template_loader: ComponentTemplateLoader) -> None:
        self._loader = template_loader

    def can_render(self, info: ComponentInfo) -> bool:
        return info.is_simple or info.module_path is None

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        template_str = self._loader.load(info)
        if template_str is None:
            return ""
        context_dict = dict(context_data)
        if request is not None:
            context_dict.setdefault("request", request)
            _merge_csrf_context(context_dict, request)
        return _render_template_string(template_str, context_dict)


class CompositeComponentRenderer:
    """Uses ``render()`` in ``component.py`` when present, otherwise the template."""

    def __init__(
        self,
        module_loader: ModuleLoader,
        template_loader: ComponentTemplateLoader,
    ) -> None:
        self._module_loader = module_loader
        self._template_loader = template_loader

    def can_render(self, info: ComponentInfo) -> bool:
        return not info.is_simple and info.module_path is not None

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
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
        template_str = self._template_loader.load(info)
        if template_str is None:
            return ""

        context_dict = dict(context_data)
        if request is not None:
            context_dict["request"] = request
            _merge_csrf_context(context_dict, request)

        _inject_component_context(info, context_dict, request)

        return _render_template_string(template_str, context_dict)

    def _fallback_to_template(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
    ) -> str:
        template_str = self._template_loader.load(info)
        if template_str is None:
            return ""
        return _render_template_string(template_str, dict(context_data))


class ComponentRenderer:
    """Picks the first renderer that accepts this component."""

    def __init__(self, strategies: Sequence[ComponentRenderStrategy]) -> None:
        self._strategies = strategies

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None = None,
    ) -> str:
        for strategy in self._strategies:
            if strategy.can_render(info):
                return strategy.render(info, context_data, request)

        return ""


class ComponentsBackend(ABC):
    """Pluggable source of component definitions (files, database, etc.)."""

    @abstractmethod
    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Metadata for ``name`` from a backend, or ``None``."""

    @abstractmethod
    def collect_visible_components(
        self,
        template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Name to metadata for components visible when rendering ``template_path``."""


class FileComponentsBackend(ComponentsBackend):
    """Load components from ``DIRS`` and from the filesystem walk in ``next.urls``."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Build registry and scanner from merged ``COMPONENTS_DIR`` / ``DIRS``."""
        self.components_dir = str(config["COMPONENTS_DIR"])
        self._extra_component_roots = component_extra_roots_from_config(config)

        self._registry = ComponentRegistry()
        self._module_loader = ModuleLoader()
        self._scanner = ComponentScanner(
            self.components_dir,
            module_loader=self._module_loader,
        )
        self._visibility_resolver = ComponentVisibilityResolver(self._registry)

        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._discover_and_register_all()
        self._loaded = True

    def _discover_and_register_all(self) -> None:
        for comp_root in self._extra_component_roots:
            self._registry.mark_as_root(comp_root)
            self._discover_in_component_root(comp_root)

    def _discover_in_component_root(self, component_root: Path) -> None:
        components = self._scanner.scan_directory(component_root, component_root, "")
        self._registry.register_many(components)

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Named component visible from ``template_path`` after discovery."""
        self._ensure_loaded()
        visible = self.collect_visible_components(template_path)
        return visible.get(name)

    def collect_visible_components(
        self,
        template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Full visibility map for ``template_path``."""
        self._ensure_loaded()
        return self._visibility_resolver.resolve_visible(template_path)


def register_components_folder_from_router_walk(
    folder: Path,
    pages_root: Path,
    scope_relative: str,
) -> None:
    """Register components for one folder discovered during the URL tree walk."""
    key = folder.resolve()
    seen = components_manager._walk_registered_folders
    if key in seen:
        return
    seen.add(key)
    components_manager._ensure_backends()
    for backend in components_manager._backends:
        if isinstance(backend, FileComponentsBackend):
            found = backend._scanner.scan_directory(folder, pages_root, scope_relative)
            backend._registry.register_many(found)
            return


class DummyBackend(ComponentsBackend):
    """Test double that stores the factory ``config`` dict on ``self``."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Keep ``config`` on ``self`` for assertions about wiring."""
        self.config = config
        self.created = True

    def get_component(
        self,
        _name: str,
        _template_path: Path,
    ) -> ComponentInfo | None:
        return None

    def collect_visible_components(
        self,
        _template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        return {}


class BoomBackend(ComponentsBackend):
    """Test double that raises from ``__init__`` for factory error handling tests."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Raise immediately so ``ComponentsManager`` logs and skips this entry."""
        _ = config
        msg = "boom"
        raise RuntimeError(msg)

    def get_component(
        self,
        _name: str,
        _template_path: Path,
    ) -> ComponentInfo | None:
        raise NotImplementedError

    def collect_visible_components(
        self,
        _template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        raise NotImplementedError


class ComponentsFactory:
    """Instantiates backends from merged ``DEFAULT_COMPONENT_BACKENDS`` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> ComponentsBackend:
        """Single backend from one config dict (``BACKEND`` class path)."""
        backend_path = config.get("BACKEND", "next.components.FileComponentsBackend")
        backend_class = import_class_cached(backend_path)
        return cast("ComponentsBackend", backend_class(config))


class ComponentsManager:
    """Loads backends from settings and merges name resolution across them."""

    def __init__(self) -> None:
        """Prepare an empty backend list and load settings on first access."""
        self._backends: list[ComponentsBackend] = []
        self._walk_registered_folders: set[Path] = set()
        self._template_loader: ComponentTemplateLoader | None = None
        self._component_renderer: ComponentRenderer | None = None

    def _ensure_render_pipeline(self) -> None:
        if self._component_renderer is not None:
            return

        ml = ModuleLoader()

        tl = ComponentTemplateLoader(ml)
        self._template_loader = tl
        simple = SimpleComponentRenderer(tl)
        composite = CompositeComponentRenderer(ml, tl)
        self._component_renderer = ComponentRenderer([composite, simple])

    def _reset_render_pipeline(self) -> None:
        self._template_loader = None
        self._component_renderer = None

    @property
    def template_loader(self) -> ComponentTemplateLoader:
        """Shared ``ComponentTemplateLoader`` for template reads."""
        self._ensure_render_pipeline()
        return cast("ComponentTemplateLoader", self._template_loader)

    @property
    def component_renderer(self) -> ComponentRenderer:
        """Simple vs composite render strategies."""
        self._ensure_render_pipeline()
        return cast("ComponentRenderer", self._component_renderer)

    def _reload_config(self) -> None:
        self._reset_render_pipeline()
        self._backends.clear()
        self._walk_registered_folders.clear()
        configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
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
        """First non-``None`` from backends in configuration order."""
        self._ensure_backends()
        for backend in self._backends:
            info = backend.get_component(name, template_path)
            if info is not None:
                return info
        return None

    def collect_visible_components(
        self, template_path: Path
    ) -> Mapping[str, ComponentInfo]:
        """Merge visible names from backends so the first wins on duplicate names."""
        self._ensure_backends()
        merged: dict[str, ComponentInfo] = {}
        for backend in self._backends:
            for name, info in backend.collect_visible_components(template_path).items():
                if name not in merged:
                    merged[name] = info
        return merged


components_manager = ComponentsManager()


def get_component(name: str, template_path: Path) -> ComponentInfo | None:
    """Delegate to ``components_manager.get_component``."""
    return components_manager.get_component(name, template_path)


def load_component_template(info: ComponentInfo) -> str | None:
    """Raw template text for ``info``."""
    return components_manager.template_loader.load(info)


def render_component(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None = None,
) -> str:
    """Render ``info`` to HTML using template context and an optional request."""
    return components_manager.component_renderer.render(info, context_data, request)
