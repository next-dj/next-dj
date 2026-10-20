"""Component renderers and render-time helpers."""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, override

from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template import Context as DjangoTemplateContext, Template
from django.utils.functional import SimpleLazyObject

from next.caches import DEFAULT_CACHE_SIZE, LruCache
from next.conf import fail_loudly
from next.deps import render_dep_cache
from next.deps.cache import DependencyCache
from next.deps.resolver import current_resolver
from next.permissions.access import check, declared
from next.permissions.errors import UnknownAccessObjectError
from next.seeding import (
    ACTION_ANCHOR_KEY,
    COLLECTOR_KEY,
    COMPONENT_MODULE_PATH_KEY,
    PAGE_MODULE_PATH_KEY,
    REQUEST_KEY,
    TEMPLATE_PATH_KEY,
)
from next.utils import stat_mtime_ns, template_edits_watched

from .context import component
from .errors import ComponentModuleImportError
from .loading import last_load_error


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from django.db.models import Model
    from django.http import HttpRequest

    from next.permissions.markers import AccessObject
    from next.static import StaticCollector

    from .context import ContextFunction
    from .info import ComponentInfo
    from .loading import ModuleLoader


# Prop names published by the call site, so a keyless context cannot take them.
COMPONENT_PROPS_CONTEXT_KEY = "_component_props"

# Keys the render path owns, so overwriting one breaks the render itself.
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        ACTION_ANCHOR_KEY,
        COLLECTOR_KEY,
        COMPONENT_MODULE_PATH_KEY,
        COMPONENT_PROPS_CONTEXT_KEY,
        PAGE_MODULE_PATH_KEY,
        REQUEST_KEY,
        TEMPLATE_PATH_KEY,
        "children",
        "csrf_token",
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

        The one place that picks between the `.djx` body and the `component` string.
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


def _source_mtimes(info: ComponentInfo) -> dict[Path, int]:
    """Stat every file a load may read for `info`, before any of them is read.

    A stat after the read would pair old text with a newer mtime and hide an edit.
    """
    mtimes: dict[Path, int] = {}
    for candidate in (info.template_path, info.module_path):
        if candidate is None or candidate in mtimes:
            continue
        mtime_ns = stat_mtime_ns(candidate)
        if mtime_ns is not None:
            mtimes[candidate] = mtime_ns
    return mtimes


class CachedComponentTemplateLoader(ComponentTemplateLoader):
    """Reuse a compiled template until the file it was read from changes.

    A render costs at most one `stat`, and a `component` string reloads via autoreload.
    """

    def __init__(
        self, module_loader: ModuleLoader, maxsize: int = DEFAULT_CACHE_SIZE
    ) -> None:
        """Bind this loader to a shared `ModuleLoader` with an empty LRU cache."""
        super().__init__(module_loader)
        self._compiled: LruCache[_SourceKey, _CompiledTemplate] = LruCache(maxsize)
        # Taken around the cache mutations alone, never around a read or a parse.
        self._lock = threading.Lock()

    @override
    def load_template(self, info: ComponentInfo) -> Template | None:
        """Return the compiled template for `info` from the cache or a fresh read.

        Keyed by the defining files and revalidated against the body file's mtime.
        """
        key = (info.template_path, info.module_path)
        entry = self._compiled.get(key)
        if entry is not None and self._is_fresh(entry):
            return entry.template
        return self._compile(key, info)

    @override
    def clear(self) -> None:
        """Drop every compiled template."""
        with self._lock:
            self._compiled.clear()

    def _is_fresh(self, entry: _CompiledTemplate) -> bool:
        if not template_edits_watched():
            return True
        return stat_mtime_ns(entry.source_path) == entry.mtime_ns

    def _compile(self, key: _SourceKey, info: ComponentInfo) -> Template | None:
        mtimes = _source_mtimes(info)
        source = self.load_source(info)
        if source is None:
            self._drop(key)
            return None

        template = Template(source.text)
        # A subclass may read a file `ComponentInfo` does not name, so stat it here.
        mtime_ns = mtimes.get(source.path)
        if mtime_ns is None:
            mtime_ns = stat_mtime_ns(source.path)
        if mtime_ns is None:
            # Without an mtime the entry could never expire, so it is not kept.
            self._drop(key)
            return template

        self._store(key, _CompiledTemplate(source.path, mtime_ns, template))
        return template

    def _drop(self, key: _SourceKey) -> None:
        """Forget the entry under `key`, whether or not one is stored."""
        with self._lock:
            self._compiled.pop(key)

    def _store(self, key: _SourceKey, entry: _CompiledTemplate) -> None:
        with self._lock:
            self._compiled[key] = entry


def _stamp_component_anchor(info: ComponentInfo, context_dict: dict[str, Any]) -> None:
    """Overwrite the form-action anchor with this component's own module path.

    Always written, so a component without `component.py` inherits no outer anchor.
    """
    context_dict[COMPONENT_MODULE_PATH_KEY] = (
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
    """Report whether a key of any type is in the slot namespace."""
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


class _ContextRun:
    """The context functions of one component render, run in order on demand.

    An access check runs the functions up to the one it reads, and the render the rest.
    """

    __slots__ = (
        "cache",
        "collector",
        "context_data",
        "functions",
        "guarded",
        "info",
        "position",
        "request",
        "stack",
    )

    def __init__(
        self,
        info: ComponentInfo,
        context_data: dict[str, Any],
        request: HttpRequest | None,
        functions: Sequence[ContextFunction],
    ) -> None:
        """Start before the first of `functions`, writing into `context_data`.

        The cache is a copy, so what one instance resolves from its props stays its own.
        """
        self.info = info
        self.context_data = context_data
        self.request = request
        self.functions = functions
        self.position = 0
        self.collector: StaticCollector | None = context_data.get(COLLECTOR_KEY)
        self.guarded = _guarded_keys(context_data)
        self.cache = DependencyCache(backing_dict=dict(render_dep_cache(request)))
        self.stack: list[str] = []

    def pending(self, key: str) -> bool:
        """Return whether a function not run yet registers under `key`."""
        return any(func.key == key for func in self.functions[self.position :])

    def run_to(self, key: str | None = None) -> None:
        """Run the functions in order until the one under `key`, or all of them."""
        functions = self.functions
        while self.position < len(functions):
            ctx_func = functions[self.position]
            self.position += 1
            self._run(ctx_func)
            if key is not None and ctx_func.key == key:
                return

    def _run(self, ctx_func: ContextFunction) -> None:
        """Run one context function and merge or store what it returns."""
        context_data = self.context_data
        collector = self.collector
        resolved = current_resolver().resolve_with_template_context(
            ctx_func.func,
            request=self.request,
            template_context=context_data,
            _cache=self.cache,
            _stack=self.stack,
        )
        if ctx_func.key is None:
            data = ctx_func.func(**resolved)
            if isinstance(data, dict):
                _reject_collisions(self.info, data, self.guarded)
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


def _context_run(
    info: ComponentInfo,
    module_path: Path,
    context_data: dict[str, Any],
    request: HttpRequest | None,
) -> _ContextRun | None:
    """Return a run over the context functions of `info`, `None` when it has none."""
    functions = component.get_functions(module_path)
    if not functions:
        return None
    return _ContextRun(info, context_data, request, functions)


def _url_object_error(model: type[Model], module_path: Path) -> TypeError:
    """Return the error an `obj=DObject[...]` bound to a `component.py` raises."""
    return TypeError(
        f"The access of {module_path} checks a permission on a "
        f"{model._meta.label} row a URL names, and a component has no URL. Name a "
        "context with component.permission_required(perm, obj='name') instead."
    )


def _granted(
    info: ComponentInfo,
    context_data: Mapping[str, Any],
    request: HttpRequest | None,
    run: _ContextRun | None = None,
) -> bool:
    """Return whether `request` may see `info`, reading an `obj=` from its context.

    A name no keyed context gives runs them all, since a keyless one may spread it.
    """
    module_path = info.module_path
    if module_path is None:
        return True
    rule = declared(module_path)
    if not rule.requirements:
        return True

    def lookup(name: AccessObject) -> object:
        if not isinstance(name, str):
            raise _url_object_error(name, module_path)
        if run is not None and run.pending(name):
            run.run_to(name)
        elif run is not None and name not in context_data:
            run.run_to()
        if name in context_data:
            return context_data[name]
        raise UnknownAccessObjectError(name, module_path)

    return check(getattr(request, "user", None), rule, lookup) is None


def _unloaded(module_path: Path) -> str:
    """Render nothing for a `component.py` that did not load, raising it when loud.

    Its access declarations are unknown, so its template would show to anyone.
    """
    error = last_load_error(module_path)
    if error is not None and fail_loudly():
        raise ComponentModuleImportError(module_path) from error
    return ""


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
            context_dict.setdefault(REQUEST_KEY, request)
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
        module_path = info.module_path
        if module_path is None:
            return ""

        module = self._module_loader.load(module_path)
        if module is None:
            return _unloaded(module_path)

        render_func = getattr(module, "render", None)
        if callable(render_func):
            if not _granted(info, context_data, request):
                return ""
            return self._render_with_function(render_func, context_data, request)

        return self._render_with_template(info, module_path, context_data, request)

    def _render_with_function(
        self,
        render_func: Callable[..., Any],
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        cache = DependencyCache()
        stack: list[str] = []

        # No provider writes to the mapping, so it passes straight through.
        resolved = current_resolver().resolve_with_template_context(
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

    def _template_context(
        self,
        info: ComponentInfo,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> dict[str, Any]:
        """Return the context a template render of `info` starts from."""
        context_dict = dict(context_data)
        _stamp_component_anchor(info, context_dict)
        if request is not None:
            context_dict[REQUEST_KEY] = request
            _merge_csrf_context(context_dict, request)
        return context_dict

    def _render_with_template(
        self,
        info: ComponentInfo,
        module_path: Path,
        context_data: Mapping[str, Any],
        request: HttpRequest | None,
    ) -> str:
        template = self._template_loader.load_template(info)
        if template is None:
            return ""

        context_dict = self._template_context(info, context_data, request)
        run = _context_run(info, module_path, context_dict, request)
        if not _granted(info, context_dict, request, run):
            return ""
        if run is not None:
            run.run_to()

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
