"""The ancestor chain of a page, its memo, and the static and request-time folds."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, NamedTuple

from next.deps.resolver import current_resolver
from next.introspect import callable_name
from next.pages.errors import PageMetadataConflictError
from next.pages.loaders import AncestorStamps, load_page_module

from .fold import (
    EMPTY_STATE,
    FoldState,
    finish,
    fold_segment,
    merge_segments,
    trace_origins,
)
from .markers import Metadata, Segment
from .normalize import normalize_metadata
from .scope import site_segment


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterable, Mapping, MutableMapping
    from pathlib import Path

    from django.http import HttpRequest

    from .registry import PageMetadataEntry, PageMetadataRegistry


class MetadataDeclaration(NamedTuple):
    """What one `page.py` declares, the dict form or the registered callable."""

    raw: object | None
    entry: PageMetadataEntry | None


def declared_metadata(
    registry: PageMetadataRegistry, file_path: Path
) -> MetadataDeclaration:
    """Return what the `page.py` at `file_path` declares, loading it on demand."""
    module, _error = load_page_module(file_path)
    return _declaration(registry, file_path, module)


def _declaration(
    registry: PageMetadataRegistry, file_path: Path, module: types.ModuleType | None
) -> MetadataDeclaration:
    """Return what `module`, the load of the `page.py` at `file_path`, declares."""
    raw = None if module is None else getattr(module, "metadata", None)
    return MetadataDeclaration(raw, registry.entry(file_path))


@dataclass(frozen=True, slots=True)
class ChainSource:
    """One `page.py` of the chain, as a segment or as the callable that yields one.

    A callable source carries an empty segment.
    """

    file_path: Path
    segment: Segment
    func: Callable[..., Any] | None = None


@dataclass(frozen=True, slots=True)
class ChainEntry:
    """The memoised chain of one page with the stamps that validate it.

    A request folds only `tail` over `prefix`, `folded` is the fold of a static chain.
    """

    ancestors: AncestorStamps
    registry_stamps: tuple[int | None, ...]
    registry_version: int
    site: Segment
    sources: tuple[ChainSource, ...]
    prefix: FoldState
    tail: tuple[ChainSource, ...]
    static: Metadata
    folded: Metadata | None
    trail: str = ""

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return the `page.py` paths of the chain, root first."""
        return self.ancestors.paths


def _page_source(
    registry: PageMetadataRegistry,
    ancestor: Path,
    module: types.ModuleType | None,
    file_path: Path,
    trail: str,
) -> ChainSource | None:
    """Return what the load of the `page.py` at `ancestor` adds to the chain."""
    raw, entry = _declaration(registry, ancestor, module)
    if raw is not None:
        if entry is not None:
            raise PageMetadataConflictError(ancestor)
        segment = normalize_metadata(raw, source=str(ancestor))
        return ChainSource(ancestor, replace(segment, trail=trail))
    if entry is not None and (entry.inherit or ancestor == file_path):
        return ChainSource(ancestor, Segment(str(ancestor), trail=trail), entry.func)
    return None


def _trail(directory: Path, root: Path) -> str:
    """Return the path of `directory` below the tree `root`, empty for the root."""
    return "" if directory == root else directory.relative_to(root).as_posix()


def _build_chain(
    registry: PageMetadataRegistry, file_path: Path, site: Segment
) -> ChainEntry:
    """Walk the ancestors inside the page tree root first and fold what each declares.

    The stamps name the loads it folded.
    """
    ancestors, modules = AncestorStamps.begin(file_path).loaded()
    paths = ancestors.paths
    registry_version = registry.version
    registry_stamps = registry.stamps(paths)
    root = paths[0].parent
    sources: list[ChainSource] = []
    trail = ""
    for ancestor, module in zip(paths, modules, strict=True):
        trail = _trail(ancestor.parent, root)
        found = _page_source(registry, ancestor, module, file_path, trail)
        if found is not None:
            sources.append(found)
    split = next(
        (index for index, source in enumerate(sources) if source.func is not None),
        len(sources),
    )
    prefix = fold_segment(EMPTY_STATE, site)
    for source in sources[:split]:
        prefix = fold_segment(prefix, source.segment)
    state = prefix
    for source in sources[split:]:
        state = fold_segment(state, source.segment)
    static = finish(state)
    tail = tuple(sources[split:])
    return ChainEntry(
        ancestors=ancestors,
        registry_stamps=registry_stamps,
        registry_version=registry_version,
        site=site,
        sources=tuple(sources),
        prefix=prefix,
        tail=tail,
        static=static,
        folded=None if tail else static,
        trail=trail,
    )


def _revalidated(
    entry: ChainEntry, registry: PageMetadataRegistry, site: Segment
) -> ChainEntry | None:
    """Return `entry` while nothing behind it moved, `None` once something did."""
    if entry.site is not site:
        return None
    ancestors = entry.ancestors.revalidated()
    if ancestors is None:
        return None
    registry_version = registry.version
    if ancestors is entry.ancestors and entry.registry_version == registry_version:
        return entry
    # A re-executed callable at an unchanged mtime keeps the old, identical object.
    if registry.stamps(entry.paths) != entry.registry_stamps:
        return None
    return replace(entry, ancestors=ancestors, registry_version=registry_version)


def chain_entry(registry: PageMetadataRegistry, file_path: Path) -> ChainEntry:
    """Return the memoised chain of `file_path`, rebuilt once a source moved."""
    site = site_segment()
    stored = registry.chain(file_path)
    entry = None if stored is None else _revalidated(stored, registry, site)
    if entry is None:
        entry = _build_chain(registry, file_path, site)
    if entry is not stored:
        registry.remember(file_path, entry)
    return entry


def _fold_tail(
    prefix: FoldState,
    tail: Iterable[ChainSource],
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
    context_data: MutableMapping[str, object],
) -> FoldState:
    """Fold `tail` over `prefix`, each callable resolved against the render context."""
    state = prefix
    active = current_resolver()
    for source in tail:
        func = source.func
        if func is None:
            state = fold_segment(state, source.segment)
            continue
        resolved = active.resolve_dependencies(
            func,
            request=request,
            _cache=dep_cache,
            _stack=[],
            _context_data=context_data,
            **url_kwargs,
        )
        result = func(**resolved)
        source_name = f"{callable_name(func)} in {source.file_path}"
        segment = normalize_metadata(result, source=source_name)
        state = fold_segment(state, replace(segment, trail=source.segment.trail))
    return state


def _overlaid(
    entry: ChainEntry, file_path: Path, overlay: Segment
) -> tuple[ChainSource, ...]:
    """Put `overlay` in the place of the page's own source, over its own dict."""
    sources = entry.sources
    rest = tuple(source for source in sources if source.file_path != file_path)
    own = sources[-1] if len(rest) < len(sources) else None
    segment = replace(overlay, trail=entry.trail)
    if own is not None and own.func is None:
        segment = merge_segments(own.segment, segment)
    return (*rest, ChainSource(file_path, segment))


def fold_chain(
    registry: PageMetadataRegistry,
    file_path: Path,
    *,
    dep_cache: dict[str, Any],
    overlay: Segment | None = None,
    request: HttpRequest | None = None,
    url_kwargs: Mapping[str, object] | None = None,
    context_data: Callable[[], MutableMapping[str, object]] | None = None,
) -> Metadata:
    """Fold the chain of `file_path`, `overlay` standing as the page's own segment.

    The overlay lies over the page's own dict and replaces its callable.
    """
    entry = chain_entry(registry, file_path)
    if overlay is None:
        if entry.folded is not None:
            return entry.folded
        start, sources = entry.prefix, entry.tail
    else:
        start = fold_segment(EMPTY_STATE, entry.site)
        sources = _overlaid(entry, file_path, overlay)
    dynamic = any(source.func is not None for source in sources)
    context = context_data() if dynamic and context_data is not None else {}
    state = _fold_tail(
        start,
        sources,
        request=request,
        url_kwargs=url_kwargs or {},
        dep_cache=dep_cache,
        context_data=context,
    )
    return finish(state)


class MetadataOrigin(NamedTuple):
    """One folded key path and the source that settled it."""

    key: str
    source: str


def metadata_origins(entry: ChainEntry) -> tuple[MetadataOrigin, ...]:
    """Name the source of every key the static fold settles, then each callable.

    A callable answers only per request, so it is listed under `*`, whatever it sets.
    """
    segments = (entry.site, *(source.segment for source in entry.sources))
    origins = [
        MetadataOrigin(key, source)
        for key, source in sorted(trace_origins(segments).items())
    ]
    origins.extend(
        MetadataOrigin("*", f"{callable_name(source.func)} in {source.file_path}")
        for source in entry.sources
        if source.func is not None
    )
    return tuple(origins)


class MetadataThunk:
    """The deferred metadata fold of one render, handed its context on each read.

    It keeps no reference to the context, which carries the thunk itself.
    """

    __slots__ = ("dep_cache", "file_path", "registry", "request", "url_kwargs")

    def __init__(
        self,
        registry: PageMetadataRegistry,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
    ) -> None:
        """Hold what the fold needs without doing any of it yet."""
        self.registry = registry
        self.file_path = file_path
        self.request = request
        self.url_kwargs = url_kwargs
        self.dep_cache = dep_cache

    def folded(self) -> Metadata | None:
        """Return the fold of a chain without callables, `None` when one must run."""
        return chain_entry(self.registry, self.file_path).folded

    def fold(self, context_data: MutableMapping[str, object]) -> Metadata:
        """Fold the chain of the render, its callables reading `context_data`."""
        return fold_chain(
            self.registry,
            self.file_path,
            dep_cache=self.dep_cache,
            request=self.request,
            url_kwargs=self.url_kwargs,
            context_data=lambda: context_data,
        )


__all__ = [
    "ChainEntry",
    "ChainSource",
    "MetadataDeclaration",
    "MetadataOrigin",
    "MetadataThunk",
    "chain_entry",
    "declared_metadata",
    "fold_chain",
    "metadata_origins",
]
