"""Resolve callable parameters from request, URL kwargs, forms, and context.

The resolver backend is pluggable via `NEXT_FRAMEWORK["DEPENDENCY_RESOLVER"]`.
"""

from __future__ import annotations

from . import signals
from .cache import REQUEST_DEP_CACHE_ATTR, RequestMemo, render_dep_cache, request_memo
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
    AsyncCompilingParameterProvider,
    ParameterProvider,
    RegisteredParameterProvider,
)
from .registry import ProviderRegistry, provider_registry
from .resolver import DependencyResolver, resolver
from .runner import SegmentRunner, run_on_request_thread, segment_runner
from .scope import RequestScope


__all__ = [
    "REQUEST_DEP_CACHE_ATTR",
    "RESERVED_KEYS",
    "AsyncCompilingParameterProvider",
    "AsyncDependencyError",
    "AsyncParameterFiller",
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
    "RegisteredParameterProvider",
    "RequestMemo",
    "RequestScope",
    "ResolutionContext",
    "SegmentRunner",
    "UnboundObjectError",
    "UnknownDependencyError",
    "provider_registry",
    "render_dep_cache",
    "request_memo",
    "resolver",
    "rows",
    "run_on_request_thread",
    "segment_runner",
    "signals",
]
