"""How a page resolves its body, the static source or what `render()` returns."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from django.http.response import HttpResponseBase

from next.deps.context import ResolutionContext
from next.deps.resolver import current_resolver
from next.deps.runner import run_segment
from next.deps.scope import bridged_scope
from next.introspect import is_coroutine_callable
from next.pages.graph import blaming
from next.pages.loaders import (
    BoundaryChain,
    build_registered_loaders,
    load_page_module,
    page_boundaries,
)
from next.pages.visits import visit_request

from .containment import PageContainment


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.pages.boundaries import Caught


@dataclass(frozen=True, slots=True)
class BodyResolution:
    """The per-request outcome of `Page.resolve_page_body`.

    `http_response` may stream, and a `dynamic` body has no source to render a zone.
    """

    body: str | None = None
    http_response: HttpResponseBase | None = None
    dynamic: bool = False


def _classified(result: object, file_path: Path) -> BodyResolution:
    """Classify what a page `render()` returned, refusing anything but a body."""
    if isinstance(result, HttpResponseBase):
        return BodyResolution(http_response=result)
    if isinstance(result, str):
        return BodyResolution(body=result, dynamic=True)
    msg = (
        f"page.py render() at {file_path} must return str or "
        f"HttpResponseBase, got {type(result).__name__}."
    )
    raise TypeError(msg)


class PageBodies(PageContainment):
    """Resolve the body of a page per request, inside its boundaries when guarded."""

    def _load_static_body(
        self, file_path: Path, module: types.ModuleType | None
    ) -> str:
        """Return the static body for `file_path` without invoking `render()`.

        No source becomes empty, so an ancestor layout still has a slot to fill.
        """
        if module is not None:
            template_attr = getattr(module, "template", None)
            if isinstance(template_attr, str):
                return template_attr
        for loader in build_registered_loaders():
            if loader.can_load(file_path):
                return loader.load_template(file_path) or ""
        return ""

    def resolve_page_body(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any],
    ) -> BodyResolution:
        """Resolve the page body per-request.

        An `HttpResponseBase` from `render()` bypasses the layout and static pipelines.
        """
        if module is not None:
            render_func = getattr(module, "render", None)
            if callable(render_func):
                return self._call_render_function(
                    render_func, file_path, request, url_kwargs or {}, dep_cache
                )
        return BodyResolution(body=self._load_static_body(file_path, module))

    async def aresolve_page_body(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any],
    ) -> BodyResolution:
        """Resolve the page body on the async path, awaiting an `async def render`.

        Any other body resolves in one blocking segment, as `resolve_page_body` does.
        """
        render_func = None if module is None else getattr(module, "render", None)
        if not is_coroutine_callable(render_func):
            return await run_segment(
                partial(
                    self.resolve_page_body,
                    file_path,
                    module,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                )
            )
        context = ResolutionContext.for_page(request, url_kwargs or {}, dep_cache)
        result = await current_resolver().acall(render_func, context)
        return _classified(result, file_path)

    def _call_render_function(
        self,
        render_func: Callable[..., object],
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
    ) -> BodyResolution:
        """Invoke `render_func` with DI-resolved arguments and classify the result."""
        resolved = current_resolver().resolve_dependencies(
            render_func, request=request, _cache=dep_cache, _stack=[], **url_kwargs
        )
        return _classified(render_func(**resolved), file_path)

    def resolve_guarded(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain | None = None,
    ) -> BodyResolution | Caught:
        """Resolve the body as `resolve_page_body` does, inside the page boundaries."""
        chain = page_boundaries(file_path) if chain is None else chain
        if not chain.errors:
            return self.resolve_page_body(
                file_path, module, request, url_kwargs, dep_cache=dep_cache
            )
        with blaming() as blamed:
            return self._contained(
                chain,
                file_path,
                blamed,
                partial(
                    self.resolve_page_body,
                    file_path,
                    module,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                ),
            )

    def authorization_outcome(
        self,
        file_path: Path,
        request: HttpRequest,
        visit_url: str | None,
        url_kwargs: Mapping[str, object] | None = None,
    ) -> tuple[HttpResponseBase | None, bool]:
        """Resolve a page body once, returning its short-circuit response and its kind.

        `render()` sees a GET visit of `visit_url`, a page without it authorizes anyone.
        """
        module, error = load_page_module(file_path)
        if error is not None:
            # Not Http404, since it names the caller URL, and continuing skips guards.
            raise error
        render_func = getattr(module, "render", None) if module is not None else None
        if not callable(render_func):
            return None, False
        visit = visit_request(request, visit_url)
        if is_coroutine_callable(render_func):
            context = ResolutionContext.for_page(visit, url_kwargs or {}, {})
            acall = partial(current_resolver().acall, render_func, context)
            resolution = _classified(bridged_scope({}, acall), file_path)
        else:
            resolution = self._call_render_function(
                render_func, file_path, visit, url_kwargs or {}, {}
            )
        return resolution.http_response, resolution.dynamic


__all__ = ["BodyResolution", "PageBodies"]
