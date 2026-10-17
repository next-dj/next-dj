"""Boundaries between the applications of a project, checked by `manage.py check`.

The names resolve lazily, so `next.signals` loads no scan engine and no grimp.
"""

import importlib
from typing import TYPE_CHECKING

from . import signals
from .errors import (
    BaselineError,
    BoundariesUnavailableError,
    ImportScanError,
    UnknownAppError,
)


if TYPE_CHECKING:
    from .baseline import Debt
    from .graph import App, Edge, EdgeKind, Graph, Tangle
    from .manager import BoundariesManager, Refrozen, Report, Scan, boundaries_manager
    from .rules import RULES, Finding, Rule


_LAZY_SOURCES_BY_MODULE: dict[str, tuple[str, ...]] = {
    "next.boundaries.baseline": ("Debt",),
    "next.boundaries.graph": ("App", "Edge", "EdgeKind", "Graph", "Tangle"),
    "next.boundaries.manager": (
        "BoundariesManager",
        "Refrozen",
        "Report",
        "Scan",
        "boundaries_manager",
    ),
    "next.boundaries.rules": ("RULES", "Finding", "Rule"),
}

_LAZY_ATTRIBUTES: dict[str, str] = {
    name: module for module, names in _LAZY_SOURCES_BY_MODULE.items() for name in names
}


__all__ = [
    "RULES",
    "App",
    "BaselineError",
    "BoundariesManager",
    "BoundariesUnavailableError",
    "Debt",
    "Edge",
    "EdgeKind",
    "Finding",
    "Graph",
    "ImportScanError",
    "Refrozen",
    "Report",
    "Rule",
    "Scan",
    "Tangle",
    "UnknownAppError",
    "boundaries_manager",
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
