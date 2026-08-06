"""The pages subsystem covering templates, context, layouts, rendering, and URLs.

This package exposes a narrow public surface. Internal helpers are
available through deep imports from the submodules (`context`,
`loaders`, `registry`, `processors`, `watch`, `manager`).
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
