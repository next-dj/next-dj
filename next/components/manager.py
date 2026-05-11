"""`ComponentsManager` and the settings_reloaded hook.

The manager loads configured backends lazily, shares a render pipeline
between them, and subscribes to `settings_reloaded` so a fresh config
rebuilds its state without reimporting this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded

from .backends import ComponentsBackend, ComponentsFactory
from .loading import ModuleLoader
from .renderers import (
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


logger = logging.getLogger(__name__)


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
        """Return the shared `ComponentTemplateLoader` used for template reads."""
        self._ensure_render_pipeline()
        return cast("ComponentTemplateLoader", self._template_loader)

    @property
    def component_renderer(self) -> ComponentRenderer:
        """Return the active `ComponentRenderer` with the configured strategies."""
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
            except Exception:
                logger.exception(
                    "Error creating component backend from config %s", config
                )
                continue
            component_backend_loaded.send(
                sender=ComponentsManager,
                backend=backend,
                config=config,
            )
            self._backends.append(backend)

    def _ensure_backends(self) -> None:
        if not self._backends:
            self._reload_config()

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
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


def _on_settings_reloaded(**_kwargs: object) -> None:
    """Rebuild component backends when framework settings reload."""
    components_manager._reload_config()


settings_reloaded.connect(_on_settings_reloaded)


__all__ = ["ComponentsManager", "components_manager"]
