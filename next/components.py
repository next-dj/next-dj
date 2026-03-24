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
from typing import TYPE_CHECKING, Any, ClassVar


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import ModuleType

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template import Context as DjangoTemplateContext, Template

from .deps import resolver


logger = logging.getLogger(__name__)

DEFAULT_COMPONENTS_DIR = "_components"
DEFAULT_PAGES_DIR = "pages"


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    """Immutable info for a discovered component (simple or composite)."""

    name: str
    scope_root: Path
    scope_relative: str
    template_path: Path | None
    module_path: Path | None
    is_simple: bool


def _load_python_module(file_path: Path) -> ModuleType | None:
    """Load Python module from file path, returning None on failure."""
    try:
        spec = importlib.util.spec_from_file_location("component_module", file_path)
        if not spec or not spec.loader:  # pragma: no cover
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, AttributeError, OSError, SyntaxError) as e:  # pragma: no cover
        logger.debug("Could not load module %s: %s", file_path, e)
        return None  # pragma: no cover
    else:  # pragma: no cover
        return module


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
    ) -> dict[str, ComponentInfo]:
        """Return mapping of name to ComponentInfo for components visible from path."""


class FileComponentsBackend(ComponentsBackend):
    """File-based component backend: scans COMPONENTS_DIR and root component dirs."""

    def __init__(
        self,
        components_dir: str = DEFAULT_COMPONENTS_DIR,
        *,
        app_dirs: bool = True,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Store backend options and init empty registry."""
        self.components_dir = components_dir
        self.app_dirs = app_dirs
        self.options = options or {}
        self._registry: list[tuple[Path, str, str, ComponentInfo]] = []
        self._root_roots: set[Path] = set()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._registry = self._discover_all()
        self._loaded = True

    def _get_pages_dir(self) -> str:  # pragma: no cover
        return str(self.options.get("PAGES_DIR", DEFAULT_PAGES_DIR))

    def _get_app_pages_roots(self) -> list[Path]:  # pragma: no cover
        result: list[Path] = []
        pages_dir = self._get_pages_dir()
        for app_name in self._get_installed_apps():
            path = self._get_app_pages_path(app_name, pages_dir)
            if path is not None:
                result.append(path.resolve())
        return result

    def _get_installed_apps(self) -> Iterable[str]:  # pragma: no cover
        for app in getattr(settings, "INSTALLED_APPS", []):
            if not app.startswith("django."):
                yield app

    def _get_app_pages_path(
        self, app_name: str, pages_dir: str
    ) -> Path | None:  # pragma: no cover
        try:
            app_module = __import__(app_name, fromlist=[""])
            if app_module.__file__ is None:
                return None
            app_path = Path(app_module.__file__).parent
            pages_path = app_path / pages_dir
            return pages_path if pages_path.exists() else None
        except (ImportError, AttributeError):
            return None

    def _get_root_component_roots(self) -> list[Path]:
        result: list[Path] = []
        opts = self.options
        if "COMPONENTS_DIRS" in opts:  # pragma: no cover
            dirs = opts["COMPONENTS_DIRS"]
            if isinstance(dirs, (list, tuple)):
                for item in dirs:
                    p = Path(item) if not isinstance(item, Path) else item
                    if p.exists():
                        result.append(p.resolve())
            return result
        if "COMPONENTS_DIR" in opts:
            p = opts["COMPONENTS_DIR"]
            p = Path(p) if not isinstance(p, Path) else p
            if p.exists():
                result.append(p.resolve())
        return result

    def _scan_component_folder(
        self,
        folder: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> list[tuple[Path, str, str, ComponentInfo]]:
        entries: list[tuple[Path, str, str, ComponentInfo]] = []
        try:
            for item in folder.iterdir():
                if item.is_file() and item.suffix == ".djx":
                    name = item.stem
                    entries.append(
                        (
                            scope_root,
                            scope_relative,
                            name,
                            ComponentInfo(
                                name=name,
                                scope_root=scope_root,
                                scope_relative=scope_relative,
                                template_path=item,
                                module_path=None,
                                is_simple=True,
                            ),
                        ),
                    )
                elif item.is_dir():
                    comp_djx = item / "component.djx"
                    comp_py = item / "component.py"
                    if comp_djx.exists() or comp_py.exists():
                        name = item.name
                        template_path: Path | None = (
                            comp_djx if comp_djx.exists() else None
                        )
                        if (
                            comp_py.exists() and template_path is None
                        ):  # pragma: no cover
                            mod = _load_python_module(comp_py)
                            if mod is not None and hasattr(mod, "component"):
                                template_path = comp_py
                        entries.append(
                            (
                                scope_root,
                                scope_relative,
                                name,
                                ComponentInfo(
                                    name=name,
                                    scope_root=scope_root,
                                    scope_relative=scope_relative,
                                    template_path=template_path,
                                    module_path=comp_py if comp_py.exists() else None,
                                    is_simple=False,
                                ),
                            ),
                        )
        except OSError as e:  # pragma: no cover
            logger.debug("Cannot scan component folder %s: %s", folder, e)
        return entries

    def _discover_in_pages_root(
        self, pages_root: Path
    ) -> list[tuple[Path, str, str, ComponentInfo]]:
        entries: list[tuple[Path, str, str, ComponentInfo]] = []
        try:
            for path in pages_root.rglob("*"):
                if not path.is_dir():
                    continue
                if path.name != self.components_dir:  # pragma: no cover
                    continue
                parent = path.parent
                try:
                    scope_relative = parent.relative_to(pages_root).as_posix()
                    if scope_relative == ".":
                        scope_relative = ""
                except ValueError:  # pragma: no cover
                    scope_relative = ""  # pragma: no cover
                entries.extend(
                    self._scan_component_folder(path, pages_root, scope_relative)
                )
        except OSError as e:  # pragma: no cover
            logger.debug("Cannot discover components under %s: %s", pages_root, e)
        return entries

    def _discover_in_component_root(
        self,
        component_root: Path,
    ) -> list[tuple[Path, str, str, ComponentInfo]]:
        return self._scan_component_folder(component_root, component_root, "")

    def _discover_all(self) -> list[tuple[Path, str, str, ComponentInfo]]:
        result: list[tuple[Path, str, str, ComponentInfo]] = []
        if self.app_dirs:
            for pages_root in self._get_app_pages_roots():
                result.extend(self._discover_in_pages_root(pages_root))
        for comp_root in self._get_root_component_roots():
            self._root_roots.add(comp_root)  # pragma: no cover
            result.extend(
                self._discover_in_component_root(comp_root)
            )  # pragma: no cover
        return result

    def _template_path_to_relative_parts(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        try:
            template_dir = template_path.parent
            rel = template_dir.relative_to(scope_root)
            parts = rel.parts
            result = [
                "/".join(parts[:i]) if i else "" for i in range(len(parts), -1, -1)
            ]
        except ValueError:  # pragma: no cover
            return None
        else:
            return result

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
    ) -> dict[str, ComponentInfo]:
        """Return name to ComponentInfo for all visible from template_path."""
        self._ensure_loaded()
        template_path = template_path.resolve()
        candidates: list[tuple[int, str, ComponentInfo]] = []
        for scope_root, scope_relative, name, info in self._registry:
            scope_rel = "" if scope_relative == "." else scope_relative
            if scope_root in self._root_roots and scope_rel == "":  # pragma: no cover
                candidates.append((0, name, info))
                continue
            parts = self._template_path_to_relative_parts(
                template_path, scope_root.resolve()
            )
            if parts is None:  # pragma: no cover
                continue
            if scope_rel not in parts:  # pragma: no cover
                continue
            depth = len(scope_rel) if scope_rel else 0  # pragma: no cover
            candidates.append((depth, name, info))
        candidates.sort(key=lambda x: (-x[0], x[1]))
        result: dict[str, ComponentInfo] = {}
        for _depth, name, info in candidates:
            if name not in result:
                result[name] = info
        return result


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
        cls._backends[name] = backend_class  # pragma: no cover

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
        return backend_class()  # pragma: no cover


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
        if not isinstance(configs, list):  # pragma: no cover
            return
        for config in configs:
            if not isinstance(config, dict):  # pragma: no cover
                continue
            try:
                backend = ComponentsFactory.create_backend(config)
                self._backends.append(backend)  # pragma: no cover
            except Exception:  # pragma: no cover
                logger.exception(
                    "Error creating component backend from config %s", config
                )  # pragma: no cover

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
        for backend in self._backends:  # pragma: no cover
            info = backend.get_component(name, template_path)  # pragma: no cover
            if info is not None:  # pragma: no cover
                return info
        return None

    def collect_visible_components(
        self, template_path: Path
    ) -> dict[str, ComponentInfo]:
        """Merge visible components from all backends. First backend wins per name."""
        self._ensure_backends()
        merged: dict[str, ComponentInfo] = {}
        for backend in self._backends:  # pragma: no cover
            for name, info in backend.collect_visible_components(
                template_path
            ).items():  # pragma: no cover
                if name not in merged:  # pragma: no cover
                    merged[name] = info
        return merged


components_manager = ComponentsManager()


def get_component(name: str, template_path: Path) -> ComponentInfo | None:
    """Return component info for name visible from template_path."""
    return components_manager.get_component(name, template_path)


def load_component_template(info: ComponentInfo) -> str | None:
    """Load template string for a component (simple .djx or composite component.djx)."""
    if info.template_path is not None and info.template_path.suffix == ".djx":
        with contextlib.suppress(OSError, UnicodeDecodeError):
            return info.template_path.read_text(encoding="utf-8")
    if info.module_path is not None:  # pragma: no cover
        mod = _load_python_module(info.module_path)
        if mod is not None and hasattr(mod, "component"):
            return getattr(mod, "component", None)
    return None


def render_component(
    info: ComponentInfo,
    context_data: dict[str, Any],
    request: HttpRequest | None = None,
) -> str:
    """Render a component for the given context."""
    out: str = ""
    if info.is_simple or info.module_path is None:
        template_str = load_component_template(info)
        if template_str is not None:
            out = Template(template_str).render(DjangoTemplateContext(context_data))
    else:
        mod = _load_python_module(info.module_path)
        if mod is None:  # pragma: no cover
            template_str = load_component_template(info)  # pragma: no cover
            if template_str is not None:  # pragma: no cover
                out = Template(template_str).render(
                    DjangoTemplateContext(context_data)
                )  # pragma: no cover
        else:
            render_func = getattr(mod, "render", None)
            if callable(render_func):
                dep_cache: dict[str, Any] = {}
                dep_stack: list[str] = []
                resolved = resolver.resolve_with_template_context(
                    render_func,
                    request=request,
                    template_context=context_data,
                    _cache=dep_cache,
                    _stack=dep_stack,
                )
                result = render_func(**resolved)
                if isinstance(result, HttpResponse):  # pragma: no cover
                    out = result.content.decode()
                elif isinstance(result, str):
                    out = result
            else:
                template_str = load_component_template(info)
                if template_str is not None:  # pragma: no cover
                    context_for_template = dict(context_data)
                    if request is not None:  # pragma: no cover
                        context_for_template["request"] = request
                    _inject_component_context(info, context_for_template, request)
                    out = Template(template_str).render(
                        DjangoTemplateContext(context_for_template)
                    )
    return out


def _inject_component_context(
    info: ComponentInfo,
    context_data: dict[str, Any],
    request: HttpRequest | None,
) -> None:
    """Run component context functions from component.py and merge into context_data."""
    if info.module_path is None:  # pragma: no cover
        return
    ctx_registry = component.get_registry_for_path(info.module_path)
    if not ctx_registry:
        return
    dep_cache: dict[str, Any] = {}
    dep_stack: list[str] = []
    for key, (func, _) in ctx_registry.items():
        resolved = resolver.resolve_with_template_context(
            func,
            request=request,
            template_context=context_data,
            _cache=dep_cache,
            _stack=dep_stack,
        )
        if key is None:
            data = func(**resolved)
            if isinstance(data, dict):
                context_data.update(data)
        else:
            context_data[key] = func(**resolved)


class ComponentContextManager:
    """Registers context functions for a composite component (component.py)."""

    def __init__(self) -> None:
        """Initialize empty registry for component context functions."""
        self._registry: dict[
            Path, dict[str | None, tuple[Callable[..., Any], bool]]
        ] = {}

    def register(
        self, file_path: Path, key: str | None, func: Callable[..., Any]
    ) -> None:
        """Register a context function for a component file."""
        path_resolved = file_path.resolve()
        reg = self._registry.setdefault(path_resolved, {})
        if isinstance(key, str) and key in resolver.EXPLICIT_RESOLVE_KEYS:
            msg = (
                f"Component context key {key!r} is reserved for dependency injection; "
                f"use another name. Reserved: {sorted(resolver.EXPLICIT_RESOLVE_KEYS)}."
            )
            raise ValueError(msg)
        if key in reg:
            existing_func, _ = reg[key]
            repeat = existing_func is func
            if not repeat:
                na = getattr(existing_func, "__name__", None)
                nb = getattr(func, "__name__", None)
                if na == nb and nb:
                    try:
                        fa = inspect.getsourcefile(existing_func)
                        fb = inspect.getsourcefile(func)
                        repeat = bool(
                            fa and fb and Path(fa).resolve() == Path(fb).resolve()
                        )
                    except (OSError, TypeError, ValueError):
                        repeat = False
            if repeat:
                reg[key] = (func, False)
                return
            dup = "unkeyed @component.context" if key is None else f"key {key!r}"
            msg = (
                f"Duplicate component context registration ({dup}) for {path_resolved}"
            )
            raise ValueError(msg)
        reg[key] = (func, False)

    def get_registry_for_path(
        self, file_path: Path
    ) -> dict[str | None, tuple[Callable[..., Any], bool]]:
        """Return context registry for a component module path."""
        return self._registry.get(file_path.resolve(), {})

    def context(  # pragma: no cover
        self,
        func_or_key: Callable[..., Any] | str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register context for the calling component module."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            frame = inspect.currentframe()
            caller_path: Path | None = None
            for _ in range(10):
                if not frame or not frame.f_back:
                    break
                f_path = frame.f_globals.get("__file__")
                if (
                    f_path
                    and "component.py" in f_path
                    and "components.py" not in f_path
                ):
                    caller_path = Path(f_path)
                    break
                frame = frame.f_back
            if caller_path is not None:  # pragma: no cover
                key_val: str | None = None
                if isinstance(func_or_key, str):
                    key_val = func_or_key
                self.register(caller_path, key_val, func)
            return func

        if callable(func_or_key):
            return decorator(func_or_key)
        return decorator


component = ComponentContextManager()
