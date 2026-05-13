"""Resolve callable parameters from request, URL kwargs, forms, and context.

The public surface covers the annotation base class `DDependencyBase`,
the `Depends` marker, the resolver and its singleton, the
`RegisteredParameterProvider` ABC, the `ParameterProvider` protocol,
and the `RESERVED_KEYS` set. Deeper helpers live under
`next.deps.cache`, `next.deps.providers`, and `next.deps.markers`.
"""

from __future__ import annotations

from . import checks, signals
from .cache import (
    REQUEST_DEP_CACHE_ATTR,
    DependencyCycleError,
    get_request_dep_cache,
)
from .context import RESERVED_KEYS, ResolutionContext
from .markers import DDependencyBase, Depends
from .providers import ParameterProvider, RegisteredParameterProvider
from .resolver import DependencyResolver, resolver


__all__ = [
    "REQUEST_DEP_CACHE_ATTR",
    "RESERVED_KEYS",
    "DDependencyBase",
    "DependencyCycleError",
    "DependencyResolver",
    "Depends",
    "ParameterProvider",
    "RegisteredParameterProvider",
    "ResolutionContext",
    "checks",
    "get_request_dep_cache",
    "resolver",
    "signals",
]
