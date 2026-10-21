"""The pages subsystem covering templates, context, layouts, rendering, and URLs.

Only `__all__` is stable, not the `Page` methods that sibling areas call.
"""

from __future__ import annotations

from . import checks, signals
from .context import Context, ContextResult
from .errors import (
    PageContextShapeError,
    PageMetadataConflictError,
    PageMetadataRequestError,
    PageMetadataShapeError,
    PageMetadataTemplateError,
    PageModuleImportError,
)
from .manager import Page, context, page
from .metadata import (
    RESET,
    HtmlMetadataRenderer,
    MetadataDict,
    MetadataRenderer,
    Replace,
    ResolvedMetadata,
    ld,
)
from .responses import CacheControl, CacheDict, HeadersDict


__all__ = [
    "RESET",
    "CacheControl",
    "CacheDict",
    "Context",
    "ContextResult",
    "HeadersDict",
    "HtmlMetadataRenderer",
    "MetadataDict",
    "MetadataRenderer",
    "Page",
    "PageContextShapeError",
    "PageMetadataConflictError",
    "PageMetadataRequestError",
    "PageMetadataShapeError",
    "PageMetadataTemplateError",
    "PageModuleImportError",
    "Replace",
    "ResolvedMetadata",
    "checks",
    "context",
    "ld",
    "page",
    "signals",
]
