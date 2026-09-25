"""The routed `page.py` files the metadata checks read, each with its static fold."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, NamedTuple

from next.checks.common import RunMemo, discover_page_registrations, get_router_manager
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.loaders import load_page_module
from next.pages.manager import page
from next.pages.metadata import chain_entry, normalize_metadata


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from django.core.checks import CheckMessage

    from next.pages.metadata import Metadata, PageMetadataEntry, Segment


class MetadataPage(NamedTuple):
    """A routed `page.py` with its raw metadata, its own segment and its static fold.

    `raw` is `None` for the callable form, `segment` and `static` on a schema failure.
    """

    url_path: str
    page_path: Path
    raw: object
    entry: PageMetadataEntry | None
    segment: Segment | None
    shape_error: PageMetadataShapeError | None
    static: Metadata | None
    declared: bool
    dynamic: bool


_metadata_pages: RunMemo[list[MetadataPage]] = RunMemo()


def loaded_metadata_pages() -> tuple[list[CheckMessage], list[MetadataPage]]:
    """Return every routed `page.py` with what it declares and what it folds to."""
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors, []
    return init_errors, _metadata_pages.get(
        router_manager,
        lambda: [
            _metadata_page(url_path, page_path)
            for url_path, page_path in discover_page_registrations(router_manager)
        ],
    )


def _metadata_page(url_path: str, page_path: Path) -> MetadataPage:
    module, _error = load_page_module(page_path)
    raw = None if module is None else getattr(module, "metadata", None)
    entry = page._metadata_registry.entry(page_path)
    if entry is not None and raw is entry.func:
        raw = None
    segment: Segment | None = None
    shape_error: PageMetadataShapeError | None = None
    if isinstance(raw, Mapping):
        try:
            segment = normalize_metadata(raw, source=str(page_path))
        except PageMetadataShapeError as exc:
            shape_error = exc
    static, declared, dynamic = _static_fold(page_path)
    return MetadataPage(
        url_path=url_path,
        page_path=page_path,
        raw=raw,
        entry=entry,
        segment=segment,
        shape_error=shape_error,
        static=static,
        declared=declared,
        dynamic=dynamic,
    )


def _static_fold(page_path: Path) -> tuple[Metadata | None, bool, bool]:
    """Return the static fold, whether the chain has a source, and whether it is live.

    A dynamic chain carries a callable, so the static fold is not what a render shows.
    """
    try:
        entry = chain_entry(page._metadata_registry, page_path)
    except (PageMetadataShapeError, PageMetadataConflictError):
        return None, False, False
    return entry.static, bool(entry.sources), entry.folded is None


def folded_pages(pages: list[MetadataPage]) -> Iterator[tuple[MetadataPage, Metadata]]:
    """Yield the pages whose chain folded, paired with the fold."""
    for entry in pages:
        if entry.static is not None:
            yield entry, entry.static


def static_pages(pages: list[MetadataPage]) -> Iterator[tuple[MetadataPage, Metadata]]:
    """Yield the folded pages no callable rewrites, the ones the static fold renders."""
    for entry, meta in folded_pages(pages):
        if not entry.dynamic:
            yield entry, meta


__all__ = ["MetadataPage", "folded_pages", "loaded_metadata_pages", "static_pages"]
