"""Boundaries between the applications of a project, under `check --tag boundaries`.

The names resolve lazily, so `next.signals` loads no scan engine and no grimp.
"""

import importlib
from typing import TYPE_CHECKING

from . import signals
from .errors import (
    BaselineError,
    ImportGraphError,
    ImportScanError,
    SyncError,
    UnknownAppError,
)


if TYPE_CHECKING:
    from .backends import AstImportGraph, GrimpImportGraph, ImportGraphBackend
    from .baseline import Debt
    from .checks import boundary_checks
    from .declared import Declaration, Declarations, Unused
    from .graph import App, Edge, EdgeKind, Graph, Tangle
    from .kernel import Kernel
    from .manager import BoundariesManager, Refrozen, Report, Scan, boundaries_manager
    from .rules import RULES, Finding, Rule
    from .sync import Change, Plan


_LAZY_SOURCES_BY_MODULE: dict[str, tuple[str, ...]] = {
    "next.boundaries.backends": (
        "AstImportGraph",
        "GrimpImportGraph",
        "ImportGraphBackend",
    ),
    "next.boundaries.baseline": ("Debt",),
    "next.boundaries.checks": ("boundary_checks",),
    "next.boundaries.declared": ("Declaration", "Declarations", "Unused"),
    "next.boundaries.graph": ("App", "Edge", "EdgeKind", "Graph", "Tangle"),
    "next.boundaries.kernel": ("Kernel",),
    "next.boundaries.manager": (
        "BoundariesManager",
        "Refrozen",
        "Report",
        "Scan",
        "boundaries_manager",
    ),
    "next.boundaries.rules": ("RULES", "Finding", "Rule"),
    "next.boundaries.sync": ("Change", "Plan"),
}

_LAZY_ATTRIBUTES: dict[str, str] = {
    name: module for module, names in _LAZY_SOURCES_BY_MODULE.items() for name in names
}


__all__ = [
    "RULES",
    "App",
    "AstImportGraph",
    "BaselineError",
    "BoundariesManager",
    "Change",
    "Debt",
    "Declaration",
    "Declarations",
    "Edge",
    "EdgeKind",
    "Finding",
    "Graph",
    "GrimpImportGraph",
    "ImportGraphBackend",
    "ImportGraphError",
    "ImportScanError",
    "Kernel",
    "Plan",
    "Refrozen",
    "Report",
    "Rule",
    "Scan",
    "SyncError",
    "Tangle",
    "UnknownAppError",
    "Unused",
    "boundaries_manager",
    "boundary_checks",
    "signals",
]


def __getattr__(name: str) -> object:
    """Resolve a name of the scan engine from its module on first use."""
    module_name = _LAZY_ATTRIBUTES.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    """List exactly the names `__all__` carries."""
    return sorted(__all__)
