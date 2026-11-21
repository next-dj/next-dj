"""Thin top-level helpers that delegate to `components_manager`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.template.loader import get_template

from next.conf.defer import defer_template

from .manager import components_manager
from .renderers import render_error_template
from .signals import component_rendered


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from .info import ComponentInfo


def get_component(name: str, template_path: Path) -> ComponentInfo | None:
    """Delegate to `components_manager.get_component`."""
    return components_manager.get_component(name, template_path)


def collect_visible_components(template_path: Path) -> Mapping[str, ComponentInfo]:
    """Delegate to `components_manager.collect_visible_components`."""
    return components_manager.collect_visible_components(template_path)


def load_component_template(info: ComponentInfo) -> str | None:
    """Return raw template text for `info`."""
    return components_manager.template_loader.load(info)


def render_component(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None = None,
) -> str:
    """Render `info` to HTML using template context and an optional request."""
    html = components_manager.component_renderer.render(info, context_data, request)
    component_rendered.send(
        sender=components_manager.__class__, info=info, template_path=info.template_path
    )
    return html


def _render_file(
    info: ComponentInfo,
    path: Path,
    context_data: Mapping[str, Any],
    request: HttpRequest | None,
) -> str:
    """Render the boundary file at `path` beside `info`, nothing when unreadable."""
    template = components_manager.template_loader.load_file(path)
    if template is None:
        return ""
    return render_error_template(info, template, context_data, request)


def _render_default(
    setting: str, context_data: Mapping[str, Any], request: HttpRequest | None
) -> str | None:
    """Render the template `DEFER[setting]` names, `None` when it names none."""
    name = defer_template(setting)
    if name is None:
        return None
    return get_template(name).render(dict(context_data), request)


def render_component_error(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None = None,
    *,
    deferred: bool = False,
) -> str:
    """Render the `error.djx` of `info`, or nothing when it has no readable one.

    A deferred component without the file renders the `DEFER["ERROR"]` default instead.
    """
    files = info.files
    path = None if files is None else files.error
    if path is not None:
        return _render_file(info, path, context_data, request)
    if not deferred:
        return ""
    return _render_default("ERROR", context_data, request) or ""


def render_component_loading(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None = None,
) -> str:
    """Render the `loading.djx` of `info`, else the `DEFER["LOADING"]` default."""
    files = info.files
    path = None if files is None else files.loading
    if path is not None:
        return _render_file(info, path, context_data, request)
    return _render_default("LOADING", context_data, request) or ""


__all__ = [
    "collect_visible_components",
    "get_component",
    "load_component_template",
    "render_component",
    "render_component_error",
    "render_component_loading",
]
