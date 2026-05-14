"""Component renderers and render-time helpers.

`ComponentTemplateLoader` reads the raw source for a component. The
Protocol `ComponentRenderStrategy` plus `SimpleComponentRenderer` and
`CompositeComponentRenderer` are the two built-in renderers chosen by
`ComponentRenderer`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol

from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template import Context as DjangoTemplateContext, Template
from django.utils.functional import SimpleLazyObject

from next.deps import get_request_dep_cache, resolver
from next.deps.cache import DependencyCache

from .context import component


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from django.http import HttpRequest

    from next.static import StaticCollector

    from .info import ComponentInfo
    from .loading import ModuleLoader


class ComponentTemplateLoader:
    """Read template source from a `.djx` file or a `component` module string."""

    def __init__(self, module_loader: ModuleLoader) -> None:
        """Bind this loader to a shared `ModuleLoader`."""
        self._module_loader = module_loader

    def load(self, info: ComponentInfo) -> str | None:
        """Return raw template text for `info` or `None` when unavailable."""
        if info.template_path is not None and info.template_path.suffix == ".djx":
            with contextlib.suppress(OSError, UnicodeDecodeError):
                return info.template_path.read_text(encoding="utf-8")

        if info.module_path is not None:
            module = self._module_loader.load(info.module_path)
            if module is not None and hasattr(module, "component"):
                return getattr(module, "component", None)

        return None


def _render_template_string(template_str: str, context_dict: dict[str, Any]) -> str:
    return Template(template_str).render(DjangoTemplateContext(context_dict))


def _merge_csrf_context(
    context_dict: dict[str, Any],
    request: HttpRequest | None,
) -> None:
    """Add a lazy `csrf_token` matching the request context processor."""
    if request is None or "csrf_token" in context_dict:
        return

    context_dict["csrf_token"] = SimpleLazyObject(lambda: get_token(request))


def _inject_component_context(
    info: ComponentInfo,
    context_data: dict[str, Any],
    request: HttpRequest | None,
) -> None:
    if info.module_path is None:
        return

    ctx_funcs = component.get_functions(info.module_path)
    if not ctx_funcs:
        return

    collector: StaticCollector | None = context_data.get("_static_collector")

    shared = get_request_dep_cache(request)
    cache = DependencyCache(backing_dict=shared) if shared else DependencyCache()
    stack: list[str] = []

    for ctx_func in ctx_funcs:
        resolved = resolver.resolve_with_template_context(
            ctx_func.func,
            request=request,
            template_context=context_data,
            _cache=cache,
            _stack=stack,
        )

        if ctx_func.key is None:
            data = ctx_func.func(**resolved)
            if isinstance(data, dict):
                context_data.update(data)
                if ctx_func.serialize and collector is not None:
                    for k, v in data.items():
                        collector.add_js_context(k, v, serializer=ctx_func.serializer)
        else:
            result = ctx_func.func(**resolved)
            context_data[ctx_func.key] = result
            if ctx_func.serialize and collector is not None:
                collector.add_js_context(
                    ctx_func.key, result, serializer=ctx_func.serializer
                )


class ComponentRenderStrategy(Protocol):
    """Optional render path for a `ComponentInfo`."""

    def can_render(self, info: ComponentInfo) -> bool:
        """Return True when this strategy handles `info`."""
        raise NotImplementedError

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Return the rendered HTML for `info`."""
        raise NotImplementedError


class SimpleComponentRenderer:
    """Uses the template string only (no `component.py`)."""

    def __init__(self, template_loader: ComponentTemplateLoader) -> None:
        """Bind this renderer to a shared `ComponentTemplateLoader`."""
        self._loader = template_loader

    def can_render(self, info: ComponentInfo) -> bool:
        """Return True for simple components and for missing module files."""
        return info.is_simple or info.module_path is None

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render `info` by plain template string rendering."""
        template_str = self._loader.load(info)
        if template_str is None:
            return ""
        context_dict = dict(context_data)
        if request is not None:
            context_dict.setdefault("request", request)
            _merge_csrf_context(context_dict, request)
        return _render_template_string(template_str, context_dict)


class CompositeComponentRenderer:
    """Uses `render()` in `component.py` when present, otherwise the template."""

    def __init__(
        self,
        module_loader: ModuleLoader,
        template_loader: ComponentTemplateLoader,
    ) -> None:
        """Bind the renderer to shared module and template loaders."""
        self._module_loader = module_loader
        self._template_loader = template_loader

    def can_render(self, info: ComponentInfo) -> bool:
        """Return True for composite components with a loadable `component.py`."""
        return not info.is_simple and info.module_path is not None

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        """Render `info` via `component.py:render` or fall back to the template."""
        if info.module_path is None:
            return ""

        module = self._module_loader.load(info.module_path)
        if module is None:
            return self._fallback_to_template(info, context_data)

        render_func = getattr(module, "render", None)
        if callable(render_func):
            return self._render_with_function(render_func, context_data, request)

        return self._render_with_template(info, context_data, request)

    def _render_with_function(
        self,
        render_func: Callable[..., Any],
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        cache = DependencyCache()
        stack: list[str] = []

        resolved = resolver.resolve_with_template_context(
            render_func,
            request=request,
            template_context=dict(context_data),
            _cache=cache,
            _stack=stack,
        )

        result = render_func(**resolved)

        if isinstance(result, HttpResponse):
            return result.content.decode()
        return str(result)

    def _render_with_template(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        template_str = self._template_loader.load(info)
        if template_str is None:
            return ""

        context_dict = dict(context_data)
        if request is not None:
            context_dict["request"] = request
            _merge_csrf_context(context_dict, request)

        _inject_component_context(info, context_dict, request)

        return _render_template_string(template_str, context_dict)

    def _fallback_to_template(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
    ) -> str:
        template_str = self._template_loader.load(info)
        if template_str is None:
            return ""
        return _render_template_string(template_str, dict(context_data))


class ComponentRenderer:
    """Picks the first renderer that accepts this component."""

    def __init__(self, strategies: Sequence[ComponentRenderStrategy]) -> None:
        """Bind the renderer to an ordered list of render strategies."""
        self._strategies = strategies

    def render(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None = None,
    ) -> str:
        """Return HTML from the first matching render strategy."""
        for strategy in self._strategies:
            if strategy.can_render(info):
                return strategy.render(info, context_data, request)

        return ""


__all__ = [
    "ComponentRenderStrategy",
    "ComponentRenderer",
    "ComponentTemplateLoader",
    "CompositeComponentRenderer",
    "SimpleComponentRenderer",
]
