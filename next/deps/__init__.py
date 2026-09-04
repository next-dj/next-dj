"""Resolve callable parameters from request, URL kwargs, forms, and context.

The public surface covers the annotation base class `DDependencyBase`, the
`Depends` marker, the `DependencyResolver` class and its singleton, the
`UnknownDependencyError` exception, the `RegisteredParameterProvider` ABC, the
`ParameterProvider` protocol, the `ProviderRegistry` and its singleton, and the
`RESERVED_KEYS` set. Deeper helpers live under `next.deps.cache`,
`next.deps.providers`, `next.deps.markers`, and `next.deps.plan`, whose
`compile_plan`, `InjectionPlan`, and `ParameterPlan` are the contract a static
verdict is compiled into.
"""

from __future__ import annotations

from . import signals
from .cache import REQUEST_DEP_CACHE_ATTR, DependencyCycleError, get_request_dep_cache
from .context import RESERVED_KEYS, ResolutionContext
from .markers import DDependencyBase, Depends
from .providers import ParameterProvider, RegisteredParameterProvider
from .registry import ProviderRegistry, provider_registry
from .resolver import DependencyResolver, UnknownDependencyError, resolver


__all__ = [
    "REQUEST_DEP_CACHE_ATTR",
    "RESERVED_KEYS",
    "DDependencyBase",
    "DependencyCycleError",
    "DependencyResolver",
    "Depends",
    "ParameterProvider",
    "ProviderRegistry",
    "RegisteredParameterProvider",
    "ResolutionContext",
    "UnknownDependencyError",
    "get_request_dep_cache",
    "provider_registry",
    "resolver",
    "signals",
]
