"""Component renderers and render-time helpers.

`ComponentTemplateLoader` reads the raw source for a component and
`CachedComponentTemplateLoader` keeps its compilation between renders.
The Protocol `ComponentRenderStrategy` plus `SimpleComponentRenderer` and
`CompositeComponentRenderer` are the two renderers `ComponentRenderer` picks.
"""

from __future__ import annotations

import contextlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, override

from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template import Context as DjangoTemplateContext, Template
from django.utils.functional import SimpleLazyObject

from next.deps import get_request_dep_cache, resolver
from next.deps.cache import DependencyCache
from next.utils import template_edits_watched

from .context import component


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from django.http import HttpRequest

    from next.static import StaticCollector

    from .info import ComponentInfo
    from .loading import ModuleLoader


# Prop names published by the call site, so a keyless context cannot take them.
COMPONENT_PROPS_CONTEXT_KEY = "_component_props"

# Keys the render path owns, so overwriting one breaks the render itself.
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        COMPONENT_PROPS_CONTEXT_KEY,
        "_static_collector",
        "children",
        "csrf_token",
        "current_component_module_path",
        "current_page_module_path",
        "current_template_path",
        "request",
    }
)

SLOT_KEY_PREFIX = "slot_"


@dataclass(frozen=True, slots=True)
class TemplateSource:
    """Template text together with the file it was read from."""

    text: str
    path: Path


class ComponentTemplateLoader:
    """Read template source from a `.djx` file or a `component` module string."""

    def __init__(self, module_loader: ModuleLoader) -> None:
        """Bind this loader to a shared `ModuleLoader`."""
        self._module_loader = module_loader

    def load_source(self, info: ComponentInfo) -> TemplateSource | None:
        """Return the template text for `info` with the file it came from.

        This is the one place that picks between the `.djx` body and the `component`
        module string, so no caller has to guess which one a render used.
        """
        if info.template_path is not None and info.template_path.suffix == ".djx":
            with contextlib.suppress(OSError, UnicodeDecodeError):
                text = info.template_path.read_text(encoding="utf-8")
                return TemplateSource(text, info.template_path)

        if info.module_path is not None:
            module = self._module_loader.load(info.module_path)
            module_text: str | None = getattr(module, "component", None)
            if module_text is not None:
                return TemplateSource(module_text, info.module_path)

        return None

    def load(self, info: ComponentInfo) -> str | None:
        """Return raw template text for `info` or `None` when unavailable."""
        source = self.load_source(info)
        return source.text if source is not None else None

    def load_template(self, info: ComponentInfo) -> Template | None:
        """Return the compiled template for `info` or `None` when unavailable."""
        source = self.load_source(info)
        if source is None:
            return None
        return Template(source.text)

    def clear(self) -> None:
        """Do nothing, because this loader holds no compiled state."""


@dataclass(frozen=True, slots=True)
class _CompiledTemplate:
    """A compiled template with the file it came from and that file's mtime."""

    source_path: Path
    mtime_ns: int
    template: Template


# The template and module files that define one component.
type _SourceKey = tuple[Path | None, Path | None]

# Components one process keeps compiled, at the bound the path-keyed caches share.
_COMPILED_TEMPLATE_CACHE_MAX_SIZE = 2048


def _stat_ns(path: Path) -> int | None:
    """Return the mtime of `path` in nanoseconds, or `None` when it does not stat."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _source_mtimes(info: ComponentInfo) -> dict[Path, int]:
    """Stat every file a load may read for `info`, before any of them is read.

    Reading first and stat-ing after would file the old text under the mtime of
    a save that landed in between, hiding that edit until the next one.
    """
    mtimes: dict[Path, int] = {}
    for candidate in (info.template_path, info.module_path):
        if candidate is None or candidate in mtimes:
            continue
        mtime_ns = _stat_ns(candidate)
        if mtime_ns is not None:
            mtimes[candidate] = mtime_ns
    return mtimes


class CachedComponentTemplateLoader(ComponentTemplateLoader):
    """Reuse a compiled template until the file it was read from changes.

    A render then pays at most one `stat` instead of a read plus a full parse,
    and an edited `.djx` still reaches the next render without a restart. A
    `component` string comes from an already imported module, so its edit
    arrives with the reload the autoreloader runs for `component.py`.
    """

    def __init__(
        self,
        module_loader: ModuleLoader,
        maxsize: int = _COMPILED_TEMPLATE_CACHE_MAX_SIZE,
    ) -> None:
        """Bind this loader to a shared `ModuleLoader` with an empty LRU cache."""
        super().__init__(module_loader)
        self._maxsize = maxsize
        self._compiled: OrderedDict[_SourceKey, _CompiledTemplate] = OrderedDict()
        # Taken around the cache mutations alone, never around a read or a parse.
        self._lock = threading.Lock()

    @override
    def load_template(self, info: ComponentInfo) -> Template | None:
        """Return the compiled template for `info` from the cache or a fresh read.

        An entry is keyed by the files that define the component and
        revalidated against the mtime of the one the body was read from.
        """
        key = (info.template_path, info.module_path)
        entry = self._compiled.get(key)
        if entry is not None and self._is_fresh(entry):
            # Use order decides only who is evicted, so a cache with room to
            # spare skips the bookkeeping every hit would otherwise pay.
            if len(self._compiled) >= self._maxsize:
                self._mark_used(key)
            return entry.template
        return self._compile(key, info)

    @override
    def clear(self) -> None:
        """Drop every compiled template."""
        with self._lock:
            self._compiled.clear()

    def _mark_used(self, key: _SourceKey) -> None:
        """Move `key` to the fresh end, unless a concurrent store already dropped it."""
        with self._lock:
            if key in self._compiled:
                self._compiled.move_to_end(key)

    def _is_fresh(self, entry: _CompiledTemplate) -> bool:
        if not template_edits_watched():
            return True
        return _stat_ns(entry.source_path) == entry.mtime_ns

    def _compile(self, key: _SourceKey, info: ComponentInfo) -> Template | None:
        mtimes = _source_mtimes(info)
        source = self.load_source(info)
        if source is None:
            self._drop(key)
            return None

        template = Template(source.text)
        # A subclass may read a file neither `ComponentInfo` field names, so a
        # path the pre-read stat missed is stat-ed here rather than left uncached.
        mtime_ns = mtimes.get(source.path)
        if mtime_ns is None:
            mtime_ns = _stat_ns(source.path)
        if mtime_ns is None:
            # Without an mtime the entry could never expire, so it is not kept.
            self._drop(key)
            return template

        self._store(key, _CompiledTemplate(source.path, mtime_ns, template))
        return template

    def _drop(self, key: _SourceKey) -> None:
        """Forget the entry under `key`, whether or not one is stored."""
        with self._lock:
            self._compiled.pop(key, None)

    def _store(self, key: _SourceKey, entry: _CompiledTemplate) -> None:
        with self._lock:
            if key not in self._compiled and len(self._compiled) >= self._maxsize:
                self._compiled.popitem(last=False)
            self._compiled[key] = entry
            self._compiled.move_to_end(key)


def _stamp_component_anchor(info: ComponentInfo, context_dict: dict[str, Any]) -> None:
    """Overwrite the form-action anchor with this component's own module path.

    Always written, so a component without a component.py never inherits
    the anchor of an enclosing component or a caller-supplied value.
    """
    context_dict["current_component_module_path"] = (
        str(info.module_path) if info.module_path is not None else None
    )


def _merge_csrf_context(
    context_dict: dict[str, Any], request: HttpRequest | None
) -> None:
    """Add a lazy `csrf_token` matching the request context processor."""
    if request is None or "csrf_token" in context_dict:
        return

    context_dict["csrf_token"] = SimpleLazyObject(lambda: get_token(request))


def _guarded_keys(context_data: dict[str, Any]) -> frozenset[str]:
    """Return the names a keyless context function may not overwrite.

    A bare `render_component` has no call site, so only the reserved keys apply.
    """
    props = context_data.get(COMPONENT_PROPS_CONTEXT_KEY)
    if isinstance(props, frozenset):
        return _RESERVED_CONTEXT_KEYS | props
    return _RESERVED_CONTEXT_KEYS


def _is_slot_key(key: object) -> bool:
    """Report whether a key lands in the slot namespace, non-strings included."""
    return isinstance(key, str) and key.startswith(SLOT_KEY_PREFIX)


def _reject_collisions(
    info: ComponentInfo, data: dict[Any, Any], guarded: frozenset[str]
) -> None:
    """Refuse a keyless dict that would silently overwrite a guarded name."""
    if data.keys().isdisjoint(guarded) and not any(_is_slot_key(key) for key in data):
        return

    conflicts = sorted(key for key in data if key in guarded or _is_slot_key(key))
    names = ", ".join(repr(key) for key in conflicts)
    msg = (
        f"Component {info.name!r} context returns {names}, reserved by the render "
        "path or by a prop of the calling tag. Register the value under an "
        "explicit @component.context key."
    )
    raise ValueError(msg)


def _inject_component_context(
    info: ComponentInfo, context_data: dict[str, Any], request: HttpRequest | None
) -> None:
    if info.module_path is None:
        return

    ctx_funcs = component.get_functions(info.module_path)
    if not ctx_funcs:
        return

    collector: StaticCollector | None = context_data.get("_static_collector")
    guarded = _guarded_keys(context_data)

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
                _reject_collisions(info, data, guarded)
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
        """Return the rendered HTML for `info`.

        A strategy copies `context_data`, so the caller's mapping is left alone.
        """
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
        template = self._loader.load_template(info)
        if template is None:
            return ""
        context_dict = dict(context_data)
        _stamp_component_anchor(info, context_dict)
        if request is not None:
            context_dict.setdefault("request", request)
            _merge_csrf_context(context_dict, request)
        return template.render(DjangoTemplateContext(context_dict))


class CompositeComponentRenderer:
    """Uses `render()` in `component.py` when present, otherwise the template."""

    def __init__(
        self, module_loader: ModuleLoader, template_loader: ComponentTemplateLoader
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

        # Nothing here writes to the context, and the resolver copies what
        # it injects, so this branch hands the mapping straight through.
        resolved = resolver.resolve_with_template_context(
            render_func,
            request=request,
            template_context=context_data,
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
        template = self._template_loader.load_template(info)
        if template is None:
            return ""

        context_dict = dict(context_data)
        _stamp_component_anchor(info, context_dict)
        if request is not None:
            context_dict["request"] = request
            _merge_csrf_context(context_dict, request)

        _inject_component_context(info, context_dict, request)

        return template.render(DjangoTemplateContext(context_dict))

    def _fallback_to_template(
        self, info: ComponentInfo, context_data: Mapping[str, Any]
    ) -> str:
        template = self._template_loader.load_template(info)
        if template is None:
            return ""
        context_dict = dict(context_data)
        _stamp_component_anchor(info, context_dict)
        return template.render(DjangoTemplateContext(context_dict))


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
    "CachedComponentTemplateLoader",
    "ComponentRenderStrategy",
    "ComponentRenderer",
    "ComponentTemplateLoader",
    "CompositeComponentRenderer",
    "SimpleComponentRenderer",
    "TemplateSource",
]
