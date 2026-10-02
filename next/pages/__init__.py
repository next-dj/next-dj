"""The pages subsystem covering templates, context, layouts, rendering, and URLs.

`__all__` is the guaranteed public surface. A few underscore-free `Page` methods serve
`next.forms` and `next.partial` as a cross-area contract with no stability guarantee.
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
    Metadata,
    MetadataDict,
    MetadataRenderer,
    Replace,
    ResolvedMetadata,
    SiteMetadataDict,
    ld,
)
from .responses import CacheDict, HeadersDict


__all__ = [
    "RESET",
    "CacheDict",
    "Context",
    "ContextResult",
    "HeadersDict",
    "HtmlMetadataRenderer",
    "Metadata",
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
    "SiteMetadataDict",
    "checks",
    "context",
    "ld",
    "page",
    "signals",
]
