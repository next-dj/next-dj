"""Resolve callable parameters from request, URL kwargs, forms, and context.

The resolver backend is pluggable via `NEXT_FRAMEWORK["DEPENDENCY_RESOLVER"]`.
"""

from __future__ import annotations

from . import signals
from .cache import REQUEST_DEP_CACHE_ATTR, get_request_dep_cache
from .context import RESERVED_KEYS, ResolutionContext
from .errors import DependencyCycleError, UnknownDependencyError
from .markers import DDependencyBase, Depends
from .providers import ParameterProvider, RegisteredParameterProvider
from .registry import ProviderRegistry, provider_registry
from .resolver import DependencyResolver, resolver


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
