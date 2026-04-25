"""Render helpers for next-dj pages and components.

Thin wrappers over `page.render` and `render_component` so tests do not
need to construct `ComponentInfo` manually or build an `HttpRequest`
just to exercise a renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.test import RequestFactory

from next.components.facade import render_component
from next.components.manager import components_manager
from next.pages.manager import page


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest


def render_page(
    file_path: Path | str,
    request: HttpRequest | None = None,
    /,
    **url_kwargs: Any,  # noqa: ANN401
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
    request: HttpRequest | None = None,
) -> str:
    """Resolve component `name` as seen from `at` and render it.

    `at` is the template path the component is referenced from. It is
    used for visibility/scoping by `components_manager.get_component`.
    Raises `LookupError` when no matching visible component is found.
    """
    anchor = Path(at) if not isinstance(at, Path) else at
    info = components_manager.get_component(name, anchor)
    if info is None:
        msg = f"Component not visible from {anchor}: {name!r}"
        raise LookupError(msg)
    return render_component(info, dict(context or {}), request=request)


__all__ = ["render_component_by_name", "render_page"]
