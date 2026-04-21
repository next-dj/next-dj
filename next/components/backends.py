"""Components backend contract, factory, and file-based implementation.

`ComponentsBackend` is the ABC for alternative component sources.
`FileComponentsBackend` is the default filesystem-based backend.
`ComponentsFactory` creates backend instances from merged
`DEFAULT_COMPONENT_BACKENDS` entries. `DummyBackend` and `BoomBackend`
are tiny doubles kept here so dotted-path resolution in tests works
through `import_class_cached`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, cast

from next.conf import import_class_cached, next_framework_settings

from .loading import ModuleLoader
from .registry import ComponentRegistry, ComponentVisibilityResolver
from .scanner import ComponentScanner, component_extra_roots_from_config


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .info import ComponentInfo


class ComponentsBackend(ABC):
    """Pluggable source of component definitions (files, database, etc.)."""

    @abstractmethod
    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Return metadata for `name` from this backend, or `None`."""

    @abstractmethod
    def collect_visible_components(
        self,
        template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Return a mapping of visible components for `template_path`."""


class FileComponentsBackend(ComponentsBackend):
    """Load components from `DIRS` and from the filesystem walk in `next.urls`."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Build registry and scanner from merged `COMPONENTS_DIR` and `DIRS`."""
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
        if not bool(getattr(next_framework_settings, "LAZY_COMPONENT_MODULES", False)):
            self.import_all_component_modules()

    def import_all_component_modules(self) -> None:
        """Load each `component.py` so decorators such as `@forms.action` run."""
        seen: set[Path] = set()
        for info in self._registry.get_all():
            mp = info.module_path
            if mp is None or mp in seen:
                continue
            seen.add(mp)
            self._module_loader.load(mp)

    def _discover_in_component_root(self, component_root: Path) -> None:
        components = self._scanner.scan_directory(component_root, component_root, "")
        self._registry.register_many(components)

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Return the named component visible from `template_path`."""
        self._ensure_loaded()
        visible = self.collect_visible_components(template_path)
        info = visible.get(name)
        if info is not None and info.module_path is not None:
            self._module_loader.load(info.module_path)
        return info

    def collect_visible_components(
        self,
        template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Return the full visibility map for `template_path`."""
        self._ensure_loaded()
        return self._visibility_resolver.resolve_visible(template_path)


class DummyBackend(ComponentsBackend):
    """Test double that stores the factory `config` dict on `self`."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Keep `config` on `self` for assertions about wiring."""
        self.config = config
        self.created = True

    def get_component(
        self,
        _name: str,
        _template_path: Path,
    ) -> ComponentInfo | None:
        """Return `None` to skip name resolution through this backend."""
        return None

    def collect_visible_components(
        self,
        _template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Return an empty mapping because this test double never registers."""
        return {}


class BoomBackend(ComponentsBackend):
    """Test double that raises from `__init__` for factory error handling tests."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Raise immediately so `ComponentsManager` logs and skips this entry."""
        _ = config
        msg = "boom"
        raise RuntimeError(msg)

    def get_component(
        self,
        _name: str,
        _template_path: Path,
    ) -> ComponentInfo | None:
        """Unreachable because construction always raises."""
        raise NotImplementedError

    def collect_visible_components(
        self,
        _template_path: Path,
    ) -> Mapping[str, ComponentInfo]:
        """Unreachable because construction always raises."""
        raise NotImplementedError


class ComponentsFactory:
    """Instantiates backends from merged `DEFAULT_COMPONENT_BACKENDS` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> ComponentsBackend:
        """Return a single backend from one config dict (`BACKEND` class path)."""
        backend_path = config.get("BACKEND", "next.components.FileComponentsBackend")
        backend_class = import_class_cached(backend_path)
        return cast("ComponentsBackend", backend_class(config))


def register_components_folder_from_router_walk(
    folder: Path,
    pages_root: Path,
    scope_relative: str,
) -> None:
    """Register components for one folder discovered during the URL tree walk."""
    from .manager import components_manager  # noqa: PLC0415

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
            for info in found:
                if info.module_path:
                    backend._module_loader.load(info.module_path)
            return


__all__ = [
    "BoomBackend",
    "ComponentsBackend",
    "ComponentsFactory",
    "DummyBackend",
    "FileComponentsBackend",
    "register_components_folder_from_router_walk",
]
