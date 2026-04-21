"""Pages subsystem: templates, context, layouts, rendering, URL patterns.

This package exposes a narrow public surface. Internal helpers are
available through deep imports from the submodules (`context`,
`loaders`, `registry`, `processors`, `watch`, `manager`).
"""

from __future__ import annotations

from . import checks, signals
from .context import Context, ContextResult
from .manager import Page, context, page


__all__ = [
    "Context",
    "ContextResult",
    "Page",
    "checks",
    "context",
    "page",
    "signals",
]
