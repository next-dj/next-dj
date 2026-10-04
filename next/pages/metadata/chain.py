"""The ancestor chain of a page, its memo, and the static and request-time folds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from next.conf.settings import fail_loudly
from next.deps.resolver import current_resolver
from next.diagnostics import FailureLog
from next.introspect import callable_name
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.loaders import AncestorStamps, load_page_module

from .fold import (
    EMPTY_STATE,
    FoldState,
    finish,
    fold_segment,
    fold_segments,
    merge_segments,
    trace_origins,
)
from .markers import REFUSED_ROBOTS, Metadata, Segment, refused_robots
from .normalize import normalize_metadata
from .scope import (
    SITE_SOURCE,
    contain_site_refusal,
    site_name_source,
    site_segment,
    site_tier,
)


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterable, Mapping, MutableMapping
    from pathlib import Path

    from django.http import HttpRequest

    from .registry import PageMetadataEntry, PageMetadataRegistry


_failures: Final = FailureLog(logging.getLogger(__name__))

type ChainRefusal = PageMetadataShapeError | PageMetadataConflictError
_REFUSALS: Final = (PageMetadataShapeError, PageMetadataConflictError)


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

    A request folds only `tail` over `prefix`. `folded` is set for a static chain.
    A chain the schema refuses carries the `refusal` and folds the site defaults under
    `REFUSED_ROBOTS`, so the refusal is memoised like any other chain.
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
    refusal: ChainRefusal | None = None

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return the `page.py` paths of the chain, root first."""
        return self.ancestors.paths


class StaticMetadata(NamedTuple):
    """The metadata of a page readable without a request, and whether it was refused."""

    metadata: Metadata
    refused: bool


class _Walk(NamedTuple):
    """The ancestor modules one build reads, with the stamps taken before reading."""

    ancestors: AncestorStamps
    modules: tuple[types.ModuleType | None, ...]
    registry_version: int
    registry_stamps: tuple[int | None, ...]


def _begin(registry: PageMetadataRegistry, file_path: Path) -> _Walk:
    """Load the ancestors of `file_path` and stamp the loads and the registry."""
    ancestors, modules = AncestorStamps.begin(file_path).loaded()
    registry_version = registry.version
    return _Walk(ancestors, modules, registry_version, registry.stamps(ancestors.paths))


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
    registry: PageMetadataRegistry, file_path: Path, site: Segment, walk: _Walk
) -> ChainEntry:
    """Fold what each ancestor of `walk` declares, root first, over the `site` segment.

    The entry records the stamps of the module loads it folded. A refused declaration
    raises.
    """
    paths = walk.ancestors.paths
    root = paths[0].parent
    sources: list[ChainSource] = []
    trail = ""
    for ancestor, module in zip(paths, walk.modules, strict=True):
        trail = _trail(ancestor.parent, root)
        found = _page_source(registry, ancestor, module, file_path, trail)
        if found is not None:
            sources.append(found)
    split = next(
        (index for index, source in enumerate(sources) if source.func is not None),
        len(sources),
    )
    prefix = fold_segments((site, *(source.segment for source in sources[:split])))
    state = fold_segments((source.segment for source in sources[split:]), prefix)
    static = finish(state)
    tail = tuple(sources[split:])
    return ChainEntry(
        ancestors=walk.ancestors,
        registry_stamps=walk.registry_stamps,
        registry_version=walk.registry_version,
        site=site,
        sources=tuple(sources),
        prefix=prefix,
        tail=tail,
        static=static,
        folded=None if tail else static,
        trail=trail,
    )


def _refused_entry(site: Segment, walk: _Walk, refusal: ChainRefusal) -> ChainEntry:
    """Return the entry of a refused chain, the site defaults under `REFUSED_ROBOTS`."""
    prefix = fold_segments((site,))
    folded = finish(prefix)
    static = replace(folded, robots=refused_robots(folded.robots))
    return ChainEntry(
        ancestors=walk.ancestors,
        registry_stamps=walk.registry_stamps,
        registry_version=walk.registry_version,
        site=site,
        sources=(),
        prefix=prefix,
        tail=(),
        static=static,
        folded=static,
        refusal=refusal,
    )


def _revalidated(
    entry: ChainEntry, registry: PageMetadataRegistry, site: Segment
) -> ChainEntry | None:
    """Return `entry` while its sources are unchanged, `None` once one changed."""
    if entry.site is not site:
        return None
    ancestors = entry.ancestors.revalidated()
    if ancestors is None:
        return None
    registry_version = registry.version
    if ancestors is entry.ancestors and entry.registry_version == registry_version:
        return entry
    # A callable re-registered under the same name and flag keeps its stamp.
    if registry.stamps(entry.paths) != entry.registry_stamps:
        return None
    return replace(entry, ancestors=ancestors, registry_version=registry_version)


def _memoised_entry(registry: PageMetadataRegistry, file_path: Path) -> ChainEntry:
    """Return the memoised chain of `file_path`, rebuilt once a source changed.

    A refused chain is memoised as an entry that carries its refusal.
    """
    site = site_segment()
    stored = registry.chain(file_path)
    entry = None if stored is None else _revalidated(stored, registry, site)
    if entry is None:
        walk = _begin(registry, file_path)
        try:
            entry = _build_chain(registry, file_path, site, walk)
        except _REFUSALS as exc:
            entry = _refused_entry(site, walk, exc)
    if entry is not stored:
        registry.remember(file_path, entry)
    return entry


def chain_entry(registry: PageMetadataRegistry, file_path: Path) -> ChainEntry:
    """Return the memoised chain of `file_path`, raising when the schema refuses it.

    A refused chain is walked again, so each call raises a new exception, since a
    re-raised instance grows its traceback and its notes.
    """
    entry = _memoised_entry(registry, file_path)
    if entry.refusal is None:
        return entry
    return _build_chain(registry, file_path, entry.site, _begin(registry, file_path))


def contained_chain(registry: PageMetadataRegistry, file_path: Path) -> ChainEntry:
    """Return the memoised chain of `file_path` for a response, containing a refusal.

    A refused chain renders the site defaults under `REFUSED_ROBOTS`. Its refusal
    raises under `DEBUG` or `STRICT_LOADING`. Otherwise every read marks the render
    degraded, and the refusal is logged at the `FailureLog` rate. A refused
    `DEFAULTS` is contained the same way, as `contain_site_refusal` describes.
    """
    entry = _memoised_entry(registry, file_path)
    refusal = entry.refusal
    if refusal is not None:
        _contain_refusal(registry, file_path, refusal)
    contain_site_refusal()
    return entry


def _contain_refusal(
    registry: PageMetadataRegistry, file_path: Path, refusal: ChainRefusal
) -> None:
    """Report the memoised `refusal` of the chain of `file_path` through `contain`."""
    try:
        if fail_loudly():
            chain_entry(registry, file_path)
        raise _fresh(refusal)
    except _REFUSALS as exc:
        _failures.contain(
            exc,
            (file_path, type(exc)),
            "The metadata chain of %s is refused (%s), so the page renders the "
            "site defaults under noindex. Run manage.py check to see what to fix.",
            file_path,
            exc,
        )


def _fresh(refusal: ChainRefusal) -> ChainRefusal:
    """Return a copy of `refusal` with no traceback, so no two raises share state.

    The memoised instance is never raised, since concurrent requests would each
    rewrite its traceback while a log handler formats it.
    """
    fresh = type(refusal).__new__(type(refusal), *refusal.args)
    vars(fresh).update(vars(refusal))
    fresh.__cause__ = refusal.__cause__
    return fresh


def static_metadata(registry: PageMetadataRegistry, file_path: Path) -> StaticMetadata:
    """Return the metadata of `file_path` readable without a request, contained.

    It reads as refused when the chain or the `DEFAULTS` tier above it is refused.
    """
    entry = contained_chain(registry, file_path)
    refused = entry.refusal is not None or site_tier().refusal is not None
    return StaticMetadata(entry.static, refused)


def _fold_tail(
    prefix: FoldState,
    tail: Iterable[ChainSource],
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
    context_data: MutableMapping[str, object],
    contain: bool,
) -> FoldState:
    """Fold `tail` over `prefix`, each callable resolved against the render context.

    With `contain`, a callable that raises or returns a refused shape is left out.
    """
    state = prefix
    active = current_resolver()
    for source in tail:
        func = source.func
        if func is None:
            state = fold_segment(state, source.segment)
            continue
        source_name = f"{callable_name(func)} in {source.file_path}"
        try:
            resolved = active.resolve_dependencies(
                func,
                request=request,
                _cache=dep_cache,
                _stack=[],
                _context_data=context_data,
                **url_kwargs,
            )
            segment = normalize_metadata(func(**resolved), source=source_name)
        except Exception as exc:
            if not contain:
                raise
            _failures.contain(
                exc,
                (source.file_path, type(exc)),
                "The @page.metadata callable %s raised %s, so the page renders "
                "without what it returns. Fix the callable, or raise Http404 for a "
                "missing object.",
                source_name,
                type(exc).__name__,
            )
            continue
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

    The overlay merges over the page's own dict and replaces its callable.
    A failing callable raises, so the caller decides how to handle the failure.
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
        contain=False,
    )
    return finish(state)


class MetadataOrigin(NamedTuple):
    """One folded key path and the source that set it."""

    key: str
    source: str


def metadata_origins(entry: ChainEntry) -> tuple[MetadataOrigin, ...]:
    """Name the source of every key the static fold sets, then each callable.

    A callable returns its keys only per request, so it is listed under `*`.
    """
    segments = (entry.site, *(source.segment for source in entry.sources))
    traced = trace_origins(segments)
    if traced.get("site_name") == SITE_SOURCE:
        traced["site_name"] = site_name_source()
    origins = [MetadataOrigin(key, source) for key, source in sorted(traced.items())]
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
        """Return the fold of a chain without callables, `None` when one must run.

        A chain the schema refuses folds as `contained_chain` describes.
        """
        return contained_chain(self.registry, self.file_path).folded

    def fold(self, context_data: MutableMapping[str, object]) -> Metadata:
        """Fold the chain of the render, its callables reading `context_data`.

        A callable that fails is left out, and the rest of the chain still renders.
        """
        entry = contained_chain(self.registry, self.file_path)
        if entry.folded is not None:
            return entry.folded
        state = _fold_tail(
            entry.prefix,
            entry.tail,
            request=self.request,
            url_kwargs=self.url_kwargs,
            dep_cache=self.dep_cache,
            context_data=context_data,
            contain=True,
        )
        return finish(state)


__all__ = [
    "REFUSED_ROBOTS",
    "ChainEntry",
    "ChainRefusal",
    "ChainSource",
    "MetadataDeclaration",
    "MetadataOrigin",
    "MetadataThunk",
    "StaticMetadata",
    "chain_entry",
    "contained_chain",
    "declared_metadata",
    "fold_chain",
    "metadata_origins",
    "static_metadata",
]
