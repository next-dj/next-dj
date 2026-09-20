"""Render helpers for next-dj pages and components.

Thin wrappers over `page.render` and `render_component`, so a test needs no
hand-built `ComponentInfo` or `HttpRequest` to exercise a renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.test import RequestFactory

from next.components.facade import render_component
from next.components.manager import components_manager
from next.components.renderers import COMPONENT_PROPS_CONTEXT_KEY
from next.pages.manager import page
from next.seeding import RenderFrame
from next.static import collect_component_assets


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest

    from next.static import StaticCollector


def render_page(
    file_path: Path | str, request: HttpRequest | None = None, /, **url_kwargs
) -> str:
    """Render the page at `file_path` and return its HTML string.

    When `request` is not provided a minimal `HttpRequest` is built via
    `RequestFactory().get("/")`. Extra `url_kwargs` are forwarded to
    `page.render` so tests can exercise parametric pages.
    """
    target = Path(file_path) if not isinstance(file_path, Path) else file_path
    req = request if request is not None else RequestFactory().get("/")
    return page.render(target, req, **url_kwargs)


def render_component_by_name(
    name: str,
    *,
    at: Path | str,
    context: Mapping[str, Any] | None = None,
    props: Mapping[str, Any] | None = None,
    request: HttpRequest | None = None,
    collector: StaticCollector | None = None,
    page_module_path: Path | str | None = None,
) -> str:
    """Render component `name` as resolved from the template path `at`.

    The seeded frame is what a nested tag composes from, and `context` outranks it.
    """
    anchor = Path(at) if not isinstance(at, Path) else at
    info = components_manager.get_component(name, anchor)
    if info is None:
        msg = f"Component not visible from {anchor}: {name!r}"
        raise LookupError(msg)
    frame = RenderFrame(
        template_path=anchor,
        page_module_path=page_module_path,
        request=request,
        collector=collector,
    )
    collect_component_assets(info, frame.collector)
    context_data: dict[str, Any] = {}
    frame.seed(context_data)
    context_data.update(context or {})
    context_data.update(props or {})
    # Publishing `context` too would guard keys a page render leaves shadowable.
    context_data[COMPONENT_PROPS_CONTEXT_KEY] = frozenset(props or ())
    return render_component(info, context_data, request=frame.request)


__all__ = ["render_component_by_name", "render_page"]
