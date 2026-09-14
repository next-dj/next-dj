"""Components backend contract and file-based implementation.

`ComponentsBackend` is the ABC for alternative component sources.
`FileComponentsBackend` is the default filesystem-based backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from next.conf import next_framework_settings

from .loading import ModuleLoader
from .registry import ComponentRegistry, ComponentVisibilityResolver
from .scanner import ComponentScanner, component_extra_roots_from_config


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from .info import ComponentInfo


# An entry that names no BACKEND means the filesystem source.
_DEFAULT_BACKEND_PATH = "next.components.FileComponentsBackend"


class ComponentsBackend(ABC):
    """Pluggable source of component definitions (files, database, etc.)."""

    @abstractmethod
    def get_component(self, name: str, template_path: Path) -> ComponentInfo | None:
        """Return metadata for `name` from this backend, or `None`."""

    @abstractmethod
    def collect_visible_components(
        self, template_path: Path
    ) -> Mapping[str, ComponentInfo]:
        """Return a mapping of visible components for `template_path`."""

    def discover(self) -> None:
        """Populate this backend from its source, once on app ready.

        The default does nothing, which suits a backend resolving names on demand.
        """
        return

    def import_component_modules(self) -> tuple[Path, ...]:
        """Execute the module of every known component and return their paths.

        Separate from `discover`, since `LAZY_COMPONENT_MODULES` leaves modules
        unexecuted until a render needs one, and decorator state cannot wait that long.
        """
        return ()

    def register_walked_folder(
        self, folder: Path, pages_root: Path, scope_relative: str
    ) -> bool:
        """Register `folder` under `scope_relative` below `pages_root`, or answer False.

        The walk stops at the first backend answering True, so a folder has one owner.
        """
        del folder, pages_root, scope_relative
        return False

    def iter_components(self) -> Iterable[ComponentInfo]:
        """Return every component this backend has registered, for diagnostics.

        The checks report duplicate names and wrong-decorator modules through this.
        """
        return ()

    def global_component_roots(self) -> Iterable[Path]:
        """Return the scope roots whose root-scope components resolve everywhere.

        A shared root makes its root-scope components visible from every template, a
        page tree does not, and the cross-root name check reads this to tell the two
        apart.
        """
        return ()

    def watch_roots(self) -> Iterable[Path]:
        """Return the trees the development watcher and link tooling observe.

        A backend that computes its roots from somewhere other than its config
        entry names them here, which is the only way they reach autoreload.
        """
        return ()


class FileComponentsBackend(ComponentsBackend):
    """Load components from `DIRS` and from the filesystem walk in `next.urls`."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Build registry and scanner from the merged `DIRS` roots.

        `COMPONENTS_DIR` is not read here. It names the folder the URL router skips
        inside a page tree, and `FileRouterBackend` reads it straight from the settings.
        """
        self._config = config
        self._extra_component_roots = component_extra_roots_from_config(config)

        self._registry = ComponentRegistry()
        self._module_loader = ModuleLoader()
        self._scanner = ComponentScanner(module_loader=self._module_loader)
        self._visibility_resolver = ComponentVisibilityResolver(self._registry)

        self._loaded = False

    @override
    def discover(self) -> None:
        """Scan every configured component root, once per backend instance."""
        self._ensure_loaded()

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
            self._import_registered_modules()

    def _distinct_module_paths(self) -> tuple[Path, ...]:
        """Return each registered `component.py` path once, in discovery order."""
        return tuple(
            dict.fromkeys(
                info.module_path
                for info in self._registry.get_all()
                if info.module_path is not None
            )
        )

    def _import_registered_modules(self) -> None:
        """Load each `component.py` so decorators such as `@forms.action` run."""
        for module_path in self._distinct_module_paths():
            self._module_loader.load(module_path)

    @override
    def import_component_modules(self) -> tuple[Path, ...]:
        """Import every discovered `component.py` and return their paths.

        The import is unconditional, so a caller that walks decorator state pays under
        `LAZY_COMPONENT_MODULES` the import the lazy mode otherwise avoids.
        """
        self._ensure_loaded()
        self._import_registered_modules()
        return self._distinct_module_paths()

    def _discover_in_component_root(self, component_root: Path) -> None:
        components = self._scanner.scan_directory(component_root, component_root, "")
        self._registry.register_many(components)

    @override
    def register_walked_folder(
        self, folder: Path, pages_root: Path, scope_relative: str
    ) -> bool:
        """Scan the folder into the registry and claim it for this backend."""
        found = self._scanner.scan_directory(folder, pages_root, scope_relative)
        self._registry.register_many(found)
        for info in found:
            if info.module_path:
                self._module_loader.load(info.module_path)
        return True

    @override
    def iter_components(self) -> Iterable[ComponentInfo]:
        """Return every discovered component, scanning the roots first."""
        self._ensure_loaded()
        return self._registry.get_all()

    @override
    def global_component_roots(self) -> Iterable[Path]:
        """Return the `DIRS` roots whose components resolve from every template."""
        self._ensure_loaded()
        return self._registry.global_roots()

    @override
    def watch_roots(self) -> tuple[Path, ...]:
        """Return the `DIRS` roots, read again rather than taken from discovery.

        A root named by `DIRS` that the disk did not hold at construction carries no
        component yet, and a watch built from that snapshot would never notice one.
        """
        return tuple(component_extra_roots_from_config(self._config))

    @override
    def get_component(self, name: str, template_path: Path) -> ComponentInfo | None:
        """Return the named component visible from `template_path`."""
        self._ensure_loaded()
        visible = self.collect_visible_components(template_path)
        info = visible.get(name)
        if info is not None and info.module_path is not None:
            self._module_loader.load(info.module_path)
        return info

    @override
    def collect_visible_components(
        self, template_path: Path
    ) -> Mapping[str, ComponentInfo]:
        """Return the full visibility map for `template_path`."""
        self._ensure_loaded()
        return self._visibility_resolver.resolve_visible(template_path)


__all__ = ["ComponentsBackend", "FileComponentsBackend"]
