"""The pages subsystem covering templates, context, layouts, rendering, and URLs.

`__all__` is the guaranteed public surface. A few underscore-free `Page` methods serve
`next.forms` and `next.partial` as a cross-area contract with no stability guarantee.
"""

from __future__ import annotations

from . import checks, signals
from .context import Context, ContextResult
from .errors import PageContextShapeError, PageModuleImportError
from .manager import Page, context, page


__all__ = [
    "Context",
    "ContextResult",
    "Page",
    "PageContextShapeError",
    "PageModuleImportError",
    "checks",
    "context",
    "page",
    "signals",
]
