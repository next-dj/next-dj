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
    PageMetadataShapeError,
    PageMetadataTemplateError,
    PageMetadataURLError,
    PageModuleImportError,
)
from .manager import Page, context, page
from .metadata import (
    HtmlMetadataRenderer,
    Metadata,
    MetadataDict,
    MetadataRenderer,
    SiteMetadataDict,
)


__all__ = [
    "Context",
    "ContextResult",
    "HtmlMetadataRenderer",
    "Metadata",
    "MetadataDict",
    "MetadataRenderer",
    "Page",
    "PageContextShapeError",
    "PageMetadataConflictError",
    "PageMetadataShapeError",
    "PageMetadataTemplateError",
    "PageMetadataURLError",
    "PageModuleImportError",
    "SiteMetadataDict",
    "checks",
    "context",
    "page",
    "signals",
]
