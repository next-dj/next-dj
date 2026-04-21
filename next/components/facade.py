"""Thin top-level helpers that delegate to `components_manager`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .manager import components_manager


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from .info import ComponentInfo


def get_component(name: str, template_path: Path) -> ComponentInfo | None:
    """Delegate to `components_manager.get_component`."""
    return components_manager.get_component(name, template_path)


def load_component_template(info: ComponentInfo) -> str | None:
    """Return raw template text for `info`."""
    return components_manager.template_loader.load(info)


def render_component(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None = None,
) -> str:
    """Render `info` to HTML using template context and an optional request."""
    return components_manager.component_renderer.render(info, context_data, request)


__all__ = ["get_component", "load_component_template", "render_component"]
