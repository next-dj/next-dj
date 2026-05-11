"""Per-`page.py` context-callable registry and layout watch helpers.

`PageContextRegistry` stores the list of context functions bound to
each `page.py` path, and merges their return values (with keyed and
dict-merge semantics) at render time. The watch helpers list
`template.djx` and `layout.djx` files under page roots for the
autoreloader and for the static finder.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from django.http import HttpRequest

from next.deps import DependencyResolver, resolver

from .context import ContextResult
from .signals import context_registered
from .watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.static.serializers import JsContextSerializer


class PageContextEntry(NamedTuple):
    """One context callable registered for a `page.py` file.

    The optional `serializer` overrides the global JS context
    serializer for the value this callable produces, but only when
    `serialize` is true. Backed by `NamedTuple` so the hot
    `register_context` path allocates a plain tuple rather than a
    frozen dataclass instance.
    """

    func: Callable[..., Any]
    inherit_context: bool
    serialize: bool
    serializer: JsContextSerializer | None = None


logger = logging.getLogger(__name__)


_MAX_ANCESTOR_WALK_DEPTH = 64


def get_layout_djx_paths_for_watch() -> set[Path]:
    """Return every `layout.djx` path under page trees."""
    result: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob("layout.djx"):
                result.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob layout.djx under %s: %s", pages_path, e)
    return result


def get_template_djx_paths_for_watch() -> set[Path]:
    """Return every `template.djx` path under page trees."""
    result: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob("template.djx"):
                result.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob template.djx under %s: %s", pages_path, e)
    return result


class PageContextRegistry:
    """Register per-`page.py` context callables and merge their output."""

    def __init__(
        self,
        resolver: DependencyResolver | None = None,
    ) -> None:
        """Initialise with an optional resolver and an empty registry."""
        self._context_registry: dict[
            Path,
            dict[str | None, PageContextEntry],
        ] = {}
        self._resolver = resolver

    def _get_resolver(self) -> DependencyResolver:
        """Return the injected resolver or the shared singleton."""
        if self._resolver is not None:
            return self._resolver
        return resolver

    def register_context(  # noqa: PLR0913
        self,
        file_path: Path,
        key: str | None,
        func: Callable[..., Any],
        *,
        inherit_context: bool = False,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
    ) -> None:
        """Bind `func` to `file_path` with keyed or dict-merge semantics."""
        self._context_registry.setdefault(file_path, {})[key] = PageContextEntry(
            func=func,
            inherit_context=inherit_context,
            serialize=serialize,
            serializer=serializer,
        )
        context_registered.send(
            sender=PageContextRegistry, file_path=file_path, key=key
        )

    def collect_context(
        self,
        file_path: Path,
        *args: object,
        **kwargs: object,
    ) -> ContextResult:
        """Merge inherited layout context with this file's context callables.

        The returned `ContextResult` separates the full template context
        from the JavaScript-serializable subset. The js_context uses
        first-registration semantics so that page-level values always
        take priority over inherited ones.
        """
        request = args[0] if args and isinstance(args[0], HttpRequest) else None
        context_data: dict[str, Any] = {}
        js_context: dict[str, Any] = {}
        js_context_serializers: dict[str, JsContextSerializer] = {}
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []

        inherited_context = self._collect_inherited_context(
            file_path, request, kwargs, dep_cache, dep_stack
        )
        context_data.update(inherited_context)

        registry = self._context_registry.get(file_path, {})
        ordered = sorted(
            registry.items(),
            key=lambda item: (item[0] is not None, str(item[0] or "")),
        )
        for key, entry in ordered:
            resolved = self._get_resolver().resolve_dependencies(
                entry.func,
                request=request,
                _cache=dep_cache,
                _stack=dep_stack,
                _context_data=context_data,
                **kwargs,
            )
            result = entry.func(**resolved)
            if key is None:
                context_data.update(result)
                if entry.serialize:
                    for k, v in result.items():
                        if k not in js_context:
                            js_context[k] = v
                            if entry.serializer is not None:
                                js_context_serializers[k] = entry.serializer
            else:
                context_data[key] = result
                if entry.serialize and key not in js_context:
                    js_context[key] = result
                    if entry.serializer is not None:
                        js_context_serializers[key] = entry.serializer

        return ContextResult(
            context_data=context_data,
            js_context=js_context,
            js_context_serializers=js_context_serializers,
        )

    def _collect_inherited_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> dict[str, Any]:
        """Return values from ancestor `page.py` callables marked `inherit_context`."""
        inherited_context = {}
        current_dir = file_path.parent

        for _ in range(_MAX_ANCESTOR_WALK_DEPTH):
            if current_dir == current_dir.parent:
                break

            layout_file = current_dir / "layout.djx"
            page_file = current_dir / "page.py"

            if layout_file.exists() and page_file.exists():
                for key, entry in self._context_registry.get(
                    page_file,
                    {},
                ).items():
                    if entry.inherit_context:
                        resolved = self._get_resolver().resolve_dependencies(
                            entry.func,
                            request=request,
                            _cache=dep_cache,
                            _stack=dep_stack,
                            **url_kwargs,
                        )
                        if key is None:
                            inherited_context.update(entry.func(**resolved))
                        else:
                            inherited_context[key] = entry.func(**resolved)

            current_dir = current_dir.parent

        return inherited_context
