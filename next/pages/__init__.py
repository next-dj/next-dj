"""The pages subsystem covering templates, context, layouts, rendering, and URLs.

The names listed in `__all__` form the guaranteed public surface.
A few underscore-free `Page` methods additionally serve `next.forms` and
`next.partial` as a cross-area contract without a stability guarantee,
as documented in the pages API reference.
"""

from __future__ import annotations

from . import checks, signals
from .context import Context, ContextResult
from .loaders import PageModuleImportError
from .manager import Page, context, page


__all__ = [
    "Context",
    "ContextResult",
    "Page",
    "PageModuleImportError",
    "checks",
    "context",
    "page",
    "signals",
]
