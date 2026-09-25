"""The ancestor chain of a page, its memo, and the static and request-time folds."""

from __future__ import annotations

from collections import ChainMap
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, cast

from next.deps.cache import shared_dep_cache
from next.deps.resolver import current_resolver
from next.introspect import callable_name
from next.pages.errors import PageMetadataConflictError
from next.pages.loaders import load_page_module, module_generation, page_tree_depth
from next.pages.paths import page_path_info

from .merge import fold_metadata
from .normalize import normalize_metadata
from .schema import Metadata, Segment, Text, TitleSpec
from .scope import site_segment


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, MutableMapping
    from pathlib import Path

    from django.http import HttpRequest

    from .registry import PageMetadataRegistry


PARENT_KEY: Final = "_next_metadata_parent"
"""The context key a metadata callable reads its folded parent from."""


@dataclass(frozen=True, slots=True)
class ChainSource:
    """One `page.py` of the chain, as a segment or as the callable that yields one.

    A callable source carries an empty segment, so the static fold reads all alike.
    """

    file_path: Path
    segment: Segment
    func: Callable[..., Any] | None = None


@dataclass(frozen=True, slots=True)
class ChainEntry:
    """The memoised chain of one page with the tokens that validate it.

    `folded` is the whole fold when no source is a callable, `static` the segments.
    """

    version: int
    generation: int
    site: Segment
    sources: tuple[ChainSource, ...]
    folded: Metadata | None
    static: Metadata


def _build_chain(
    registry: PageMetadataRegistry, file_path: Path, site: Segment
) -> ChainEntry:
    """Walk the ancestors inside the page tree root first and fold what each declares.

    Both tokens are read before the walk, so a module the walk loads forces a rebuild.
    A callable named `metadata` is the module attribute too, and counts as the callable.
    """
    version = registry.version
    generation = module_generation()
    in_tree = page_path_info(file_path).ancestors[: page_tree_depth(file_path.parent)]
    sources: list[ChainSource] = []
    dynamic = False
    for ancestor in reversed(in_tree):
        module, _error = load_page_module(ancestor)
        raw = None if module is None else getattr(module, "metadata", None)
        entry = registry.entry(ancestor)
        if entry is not None and raw is entry.func:
            raw = None
        if raw is not None:
            if entry is not None:
                raise PageMetadataConflictError(ancestor)
            segment = normalize_metadata(raw, source=str(ancestor))
            sources.append(ChainSource(ancestor, segment))
        elif entry is not None and (entry.inherit or ancestor == file_path):
            sources.append(ChainSource(ancestor, Segment(str(ancestor)), entry.func))
            dynamic = True
    static = fold_metadata((site, *(source.segment for source in sources)))
    return ChainEntry(
        version=version,
        generation=generation,
        site=site,
        sources=tuple(sources),
        folded=None if dynamic else static,
        static=static,
    )


def chain_entry(registry: PageMetadataRegistry, file_path: Path) -> ChainEntry:
    """Return the memoised chain of `file_path`, rebuilt once any token has moved."""
    site = site_segment()
    entry = registry.chain(file_path)
    if (
        entry is None
        or entry.version != registry.version
        or entry.generation != module_generation()
        or entry.site is not site
    ):
        entry = _build_chain(registry, file_path, site)
        registry.remember(file_path, entry)
    return entry


def _fold_sources(
    site: Segment,
    sources: Iterable[ChainSource],
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
    context_data: MutableMapping[str, object],
) -> Metadata:
    """Fold `sources` over the settings tier, each callable over the fold before it."""
    acc: list[Segment] = [site]
    active = current_resolver()
    for source in sources:
        func = source.func
        if func is None:
            acc.append(source.segment)
            continue
        parent: dict[str, object] = {PARENT_KEY: fold_metadata(acc)}
        resolved = active.resolve_dependencies(
            func,
            request=request,
            _cache=dep_cache,
            _stack=[],
            _context_data=ChainMap(parent, context_data),
            **url_kwargs,
        )
        result = func(**resolved)
        source_name = f"{callable_name(func)} in {source.file_path}"
        acc.append(normalize_metadata(result, source=source_name))
    return fold_metadata(acc)


def chain_title(
    registry: PageMetadataRegistry,
    file_path: Path,
    text: Text,
    *,
    request: HttpRequest | None = None,
    url_kwargs: Mapping[str, object] | None = None,
    context_data: Callable[[], MutableMapping[str, object]] | None = None,
) -> Text:
    """Return the title the page would render if its own `page.py` said `text`.

    An inherited callable runs as in the render, its context built only on demand.
    """
    entry = chain_entry(registry, file_path)
    own = Segment(str(file_path), title=TitleSpec(text=text))
    sources: list[ChainSource] = []
    dynamic = False
    for source in entry.sources:
        if source.file_path != file_path:
            sources.append(source)
            dynamic = dynamic or source.func is not None
        elif source.func is None:
            own = replace(source.segment, title=own.title)
    sources.append(ChainSource(file_path, own))
    context = context_data() if dynamic and context_data is not None else {}
    folded = _fold_sources(
        entry.site,
        sources,
        request=request,
        url_kwargs=url_kwargs or {},
        dep_cache=shared_dep_cache(request),
        context_data=context,
    )
    return cast("Text", folded.title)


class MetadataThunk:
    """The deferred metadata resolve of one render, handed its context on each read.

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
        """Hold what the resolve needs without doing any of it yet."""
        self.registry = registry
        self.file_path = file_path
        self.request = request
        self.url_kwargs = url_kwargs
        self.dep_cache = dep_cache

    def folded(self) -> Metadata | None:
        """Return the fold of a chain without callables, `None` when one must run."""
        return chain_entry(self.registry, self.file_path).folded

    def resolve(self, context_data: MutableMapping[str, object]) -> Metadata:
        """Fold the chain of the render, its callables reading `context_data`.

        A callable sees the fold before it as its parent and shares the render's cache.
        """
        entry = chain_entry(self.registry, self.file_path)
        folded = entry.folded
        if folded is not None:
            return folded
        return _fold_sources(
            entry.site,
            entry.sources,
            request=self.request,
            url_kwargs=self.url_kwargs,
            dep_cache=self.dep_cache,
            context_data=context_data,
        )


__all__ = [
    "PARENT_KEY",
    "ChainEntry",
    "ChainSource",
    "MetadataThunk",
    "chain_entry",
    "chain_title",
]
