"""The ancestor chain of a page, its memo, and the static and request-time folds."""

from __future__ import annotations

from collections import ChainMap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from next.deps.resolver import current_resolver
from next.introspect import callable_name
from next.pages.errors import PageMetadataConflictError
from next.pages.loaders import _load_python_module_memo, module_generation
from next.pages.paths import page_path_info

from .defaults import site_segment
from .merge import fold_metadata
from .schema import Metadata, Segment, Text, TitleSpec, normalize_metadata


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, MutableMapping
    from pathlib import Path

    from django.http import HttpRequest

    from .registry import PageMetadataRegistry


PARENT_KEY: Final = "_next_metadata_parent"
"""The context key a metadata callable reads its folded parent from."""


@dataclass(frozen=True, slots=True)
class ChainSource:
    """One `page.py` of the chain, as a segment or as the callable that yields one.

    A callable source carries an empty segment, so the static fold reads every source
    alike and only the request-time fold has to tell the two apart.
    """

    file_path: Path
    segment: Segment
    func: Callable[..., Any] | None = None


@dataclass(frozen=True, slots=True)
class ChainEntry:
    """The memoised chain of one page with the three tokens that validate it.

    `folded` is the whole fold when no source is a callable, and `static` is the fold
    of the segments alone, which the checks and the sitemap read without a request.
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
    """Walk the ancestors root first and fold what each one declares.

    Both tokens are read before the walk, so a module the walk itself loads or
    registers moves them past the entry and the next read rebuilds it settled. A
    callable named `metadata` is the module attribute too, and counts as the callable.
    """
    version = registry.version
    generation = module_generation()
    sources: list[ChainSource] = []
    dynamic = False
    for ancestor in reversed(page_path_info(file_path).ancestors):
        module = _load_python_module_memo(ancestor)
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
    entry = registry._chains.get(file_path)
    if (
        entry is None
        or entry.version != registry.version
        or entry.generation != module_generation()
        or entry.site is not site
    ):
        entry = _build_chain(registry, file_path, site)
        registry._chains[file_path] = entry
    return entry


def static_metadata(registry: PageMetadataRegistry, file_path: Path) -> Metadata:
    """Return the fold of the settings tier and every static segment of the chain."""
    return chain_entry(registry, file_path).static


def templated_title(
    registry: PageMetadataRegistry,
    file_path: Path,
    text: Text,
    *,
    absolute: bool = False,
) -> Text:
    """Return the title the page would render if its own `page.py` said `text`."""
    entry = chain_entry(registry, file_path)
    segments = [entry.site]
    segments.extend(
        source.segment for source in entry.sources if source.file_path != file_path
    )
    spec = TitleSpec(absolute=text) if absolute else TitleSpec(text=text)
    segments.append(Segment(str(file_path), title=spec))
    return cast("Text", fold_metadata(segments).title)


def resolve_metadata(
    registry: PageMetadataRegistry,
    file_path: Path,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
    context_data: MutableMapping[str, object],
) -> Metadata:
    """Fold the chain of `file_path` for one request, running its callables.

    A callable sees the fold of everything before it as its parent, and shares the
    dependency cache of the request with the context merge and `render()`.
    """
    entry = chain_entry(registry, file_path)
    folded = entry.folded
    if folded is not None:
        return folded
    acc: list[Segment] = [entry.site]
    active = current_resolver()
    for source in entry.sources:
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


class MetadataThunk:
    """The deferred metadata resolve of one render, folded once by the first reader."""

    __slots__ = (
        "_resolved",
        "context_data",
        "dep_cache",
        "file_path",
        "registry",
        "request",
        "url_kwargs",
    )

    def __init__(
        self,
        registry: PageMetadataRegistry,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        context_data: MutableMapping[str, object],
    ) -> None:
        """Hold what the resolve needs without doing any of it yet."""
        self.registry = registry
        self.file_path = file_path
        self.request = request
        self.url_kwargs = url_kwargs
        self.dep_cache = dep_cache
        self.context_data = context_data
        self._resolved: Metadata | None = None

    def resolve(self) -> Metadata:
        """Return the metadata of the render, folding the chain on the first call."""
        resolved = self._resolved
        if resolved is None:
            resolved = resolve_metadata(
                self.registry,
                self.file_path,
                request=self.request,
                url_kwargs=self.url_kwargs,
                dep_cache=self.dep_cache,
                context_data=self.context_data,
            )
            self._resolved = resolved
        return resolved


__all__ = [
    "PARENT_KEY",
    "ChainEntry",
    "ChainSource",
    "MetadataThunk",
    "chain_entry",
    "resolve_metadata",
    "static_metadata",
    "templated_title",
]
