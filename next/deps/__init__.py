"""Resolve callable parameters from request, URL kwargs, forms, and context.

The resolver backend is pluggable via `NEXT_FRAMEWORK["DEPENDENCY_RESOLVER"]`.
"""

from __future__ import annotations

from . import signals
from .cache import bind_dep_cache, render_dep_cache
from .context import RESERVED_KEYS, ResolutionContext
from .errors import (
    AsyncDependencyError,
    DependencyCycleError,
    MissingKeyError,
    UnboundObjectError,
    UnknownDependencyError,
)
from .loader import Loader, rows
from .markers import DDependencyBase, Depends
from .objects import DObject
from .plan import AsyncParameterFiller, ParameterFiller
from .providers import (
    READS_EVERY,
    READS_NOTHING,
    AnsweringParameterProvider,
    AsyncCompilingParameterProvider,
    ContextReads,
    ParameterProvider,
    ReachingParameterProvider,
    ReadingParameterProvider,
    RegisteredParameterProvider,
)
from .registry import ProviderRegistry, provider_registry
from .resolver import DependencyResolver, resolver


__all__ = [
    "READS_EVERY",
    "READS_NOTHING",
    "RESERVED_KEYS",
    "AnsweringParameterProvider",
    "AsyncCompilingParameterProvider",
    "AsyncDependencyError",
    "AsyncParameterFiller",
    "ContextReads",
    "DDependencyBase",
    "DObject",
    "DependencyCycleError",
    "DependencyResolver",
    "Depends",
    "Loader",
    "MissingKeyError",
    "ParameterFiller",
    "ParameterProvider",
    "ProviderRegistry",
    "ReachingParameterProvider",
    "ReadingParameterProvider",
    "RegisteredParameterProvider",
    "ResolutionContext",
    "UnboundObjectError",
    "UnknownDependencyError",
    "bind_dep_cache",
    "provider_registry",
    "render_dep_cache",
    "resolver",
    "rows",
    "signals",
]
