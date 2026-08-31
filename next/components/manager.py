"""`ComponentsManager` and the settings hooks that invalidate its caches.

The manager loads configured backends lazily, shares a render pipeline
between them, and subscribes to `settings_reloaded` so a fresh config
drops the cached state without reimporting this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.core.signals import setting_changed

from next.backends import backend_entries, load_backends
from next.conf.signals import settings_reloaded

from .backends import _DEFAULT_BACKEND_PATH, ComponentsBackend
from .loading import ModuleLoader
from .renderers import (
    CachedComponentTemplateLoader,
    ComponentRenderer,
    ComponentTemplateLoader,
    CompositeComponentRenderer,
    SimpleComponentRenderer,
)
from .signals import component_backend_loaded


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .info import ComponentInfo


class ComponentsManager:
    """Loads backends from settings and merges name resolution across them."""

    def __init__(self) -> None:
        """Prepare an empty backend list and load settings on first access."""
        self._backends: list[ComponentsBackend] = []
        # An empty list is a legitimate load result, so only a flag knows.
        self._loaded: bool = False
        self._walk_registered_folders: set[Path] = set()
        self._template_loader: ComponentTemplateLoader | None = None
        self._component_renderer: ComponentRenderer | None = None

    def _ensure_render_pipeline(self) -> None:
        if self._component_renderer is not None:
            return

        ml = ModuleLoader()

        tl = CachedComponentTemplateLoader(ml)
        self._template_loader = tl
        simple = SimpleComponentRenderer(tl)
        composite = CompositeComponentRenderer(ml, tl)
        self._component_renderer = ComponentRenderer([composite, simple])

    def _reset_render_pipeline(self) -> None:
        self._template_loader = None
        self._component_renderer = None

    @property
    def template_loader(self) -> ComponentTemplateLoader:
        """Return the shared `ComponentTemplateLoader` used for template reads."""
        self._ensure_render_pipeline()
        return cast("ComponentTemplateLoader", self._template_loader)

    def clear_template_caches(self) -> None:
        """Drop compiled component templates without rebuilding the pipeline.

        Outside `DEBUG` the loader reuses a compiled template without checking its
        source, so a caller that rewrote a component on disk drops it here.
        """
        self.template_loader.clear()

    @property
    def component_renderer(self) -> ComponentRenderer:
        """Return the active `ComponentRenderer` with the configured strategies."""
        self._ensure_render_pipeline()
        return cast("ComponentRenderer", self._component_renderer)

    def _invalidate(self) -> None:
        """Drop cached backends and the render pipeline without rebuilding.

        Settings reload far more often than a component renders, so the rebuild waits
        for the next access instead of running in the signal receiver.
        """
        self._reset_render_pipeline()
        self._backends = []
        self._walk_registered_folders.clear()
        self._loaded = False

    def reload(self) -> None:
        """Rebuild the backends from the current `NEXT_FRAMEWORK` settings.

        The render pipeline and the router-walk claims go with the old backends, so the
        next render resolves against the freshly configured sources.
        """
        self._invalidate()
        self._backends = load_backends(
            backend_entries("COMPONENT_BACKENDS"),
            base=ComponentsBackend,
            default=_DEFAULT_BACKEND_PATH,
            signal=component_backend_loaded,
        )
        self._loaded = True

    def _ensure_backends(self) -> None:
        if not self._loaded:
            self.reload()

    @property
    def backends(self) -> tuple[ComponentsBackend, ...]:
        """Return the configured backends in consultation order."""
        self._ensure_backends()
        return tuple(self._backends)

    def _claim_router_walk_folder(self, folder: Path) -> bool:
        """Return True the first time a router-walk folder is claimed.

        Owns the dedup set so a repeated walk over the same folder
        registers its components only once.
        """
        key = folder.resolve()
        if key in self._walk_registered_folders:
            return False
        self._walk_registered_folders.add(key)
        return True

    def register_router_walk_folder(
        self, folder: Path, pages_root: Path, scope_relative: str
    ) -> None:
        """Register components for one folder discovered during a page-tree walk.

        The route trail the folder sits on becomes its scope, so the components
        resolve only for templates under that part of the tree. The folder goes
        to the first backend whose `register_walked_folder` claims it.
        """
        # Backends first, because loading them clears the claim set and a claim
        # taken before that would be forgotten as soon as it was made.
        self._ensure_backends()
        if not self._claim_router_walk_folder(folder):
            return
        for backend in self._backends:
            if backend.register_walked_folder(folder, pages_root, scope_relative):
                return

    def get_component(self, name: str, template_path: Path) -> ComponentInfo | None:
        """Return the first non-`None` match from configured backends."""
        self._ensure_backends()
        for backend in self._backends:
            info = backend.get_component(name, template_path)
            if info is not None:
                return info
        return None

    def collect_visible_components(
        self, template_path: Path
    ) -> Mapping[str, ComponentInfo]:
        """Merge visible names across backends so the first wins on duplicates."""
        self._ensure_backends()
        merged: dict[str, ComponentInfo] = {}
        for backend in self._backends:
            for name, info in backend.collect_visible_components(template_path).items():
                if name not in merged:
                    merged[name] = info
        return merged


components_manager = ComponentsManager()


def register_components_folder_from_router_walk(
    folder: Path, pages_root: Path, scope_relative: str
) -> None:
    """Register components for one folder into the live components manager."""
    components_manager.register_router_walk_folder(folder, pages_root, scope_relative)


def _on_settings_reloaded(**kwargs) -> None:
    """Drop cached component backends when framework settings reload."""
    components_manager._invalidate()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the render pipeline when Django reports a `TEMPLATES` change.

    A compiled template pins the engine that built it, and the `settings_reloaded`
    hook beside this one fires for `NEXT_FRAMEWORK` alone.
    """
    if setting == "TEMPLATES":
        components_manager._reset_render_pipeline()


settings_reloaded.connect(_on_settings_reloaded)
setting_changed.connect(_on_setting_changed)


__all__ = [
    "ComponentsManager",
    "components_manager",
    "register_components_folder_from_router_walk",
]
