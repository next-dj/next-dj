"""Dependency resolution for next.dj.

Resolves function parameters from request context (request, URL kwargs, form)
by inspecting signatures and matching parameters to known sources. Enables
FastAPI-style dependency injection so callables declare only the arguments
they need.

D-markers use a common base Generic so that DI markers can be validated by the
linter. Import the base from this module when defining new markers.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Protocol,
    TypeVar,
    cast,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from django.http import HttpRequest


T = TypeVar("T")

# Reserved keys that cannot be used as context keys
RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    {"request", "form", "_cache", "_stack", "_context_data"}
)


class DDependencyBase[T]:
    """Base for all D-markers.

    All DI markers (e.g. DForm, DUrl) must inherit from this.
    """

    __slots__ = ()


# Sentinel for "dependency currently being resolved" to detect cycles
_IN_PROGRESS: object = object()
_CACHE_MISS: object = object()


class DependencyCycleError(Exception):
    """Raised when a circular dependency is detected between registered callables."""

    def __init__(self, cycle: list[str]) -> None:
        """Store cycle path and set exception message."""
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


class DependencyCache:
    """Cache for resolved dependencies with cycle detection."""

    def __init__(self, backing_dict: dict[str, Any] | None = None) -> None:
        """Initialize cache, optionally wrapping an existing dict for compatibility."""
        self._cache: dict[str, Any] = backing_dict if backing_dict is not None else {}
        self._in_progress: set[str] = set()
        self._owns_cache = backing_dict is None

    def get(self, key: str) -> object:
        """Get cached value for key."""
        if key in self._in_progress:
            return _IN_PROGRESS
        if key in self._cache:
            return self._cache[key]
        return _CACHE_MISS

    def set(self, key: str, value: object) -> None:
        """Store resolved value in cache."""
        self._cache[key] = value
        self._in_progress.discard(key)

    def mark_in_progress(self, key: str) -> None:
        """Mark dependency as currently being resolved."""
        self._in_progress.add(key)

    def unmark_in_progress(self, key: str) -> None:
        """Remove in-progress marker for dependency."""
        self._in_progress.discard(key)

    def is_in_progress(self, key: str) -> bool:
        """Check if dependency is currently being resolved."""
        return key in self._in_progress

    def __len__(self) -> int:
        """Return number of cached values."""
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Check if key is in cache."""
        return key in self._cache


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    """Immutable context for dependency resolution."""

    request: HttpRequest | None
    form: object | None
    url_kwargs: Mapping[str, Any]
    context_data: Mapping[str, Any]
    cache: DependencyCache
    stack: list[str] = field(default_factory=list)


class DependencyResolver:
    """Resolves function parameters from context using a chain of providers."""

    # Names passed as their own kwargs to resolve_dependencies; anything else in
    # **context is treated like URL kwargs for providers.
    EXPLICIT_RESOLVE_KEYS: ClassVar[frozenset[str]] = RESERVED_KEYS

    def __get__(self, obj: object, owner: type[object]) -> DependencyResolver:
        """Return self when used as class attribute on RegisteredParameterProvider."""
        return self

    def __init__(
        self, *providers: ParameterProvider | RegisteredParameterProvider
    ) -> None:
        """Initialize with optional providers.

        With no args, chain is built lazily from registry on first resolve.
        """
        self._dependency_callables: dict[str, Callable[..., Any]] = {}
        self._providers: list[ParameterProvider] = list(providers)
        self._providers_loaded = bool(providers)

    def _get_providers(self) -> list[ParameterProvider]:
        """Build providers from the registry and inject the resolver when required."""
        import next.forms  # noqa: PLC0415
        import next.pages  # noqa: PLC0415
        import next.urls  # noqa: F401, PLC0415

        result: list[ParameterProvider] = []
        for cls in RegisteredParameterProvider._registry:
            sig = inspect.signature(cls)
            if "resolver" in sig.parameters:
                result.append(cls(resolver=self))
            else:
                result.append(cls())
        return result

    def _ensure_providers(self) -> None:
        """Load providers from registry on first use."""
        if not self._providers_loaded:
            self._providers.extend(self._get_providers())
            self._providers_loaded = True

    def _should_skip_parameter(self, param: inspect.Parameter) -> bool:
        """Check if parameter should be skipped during resolution."""
        return param.name in ("self", "cls") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )

    def _resolve_parameter(
        self, param: inspect.Parameter, context: ResolutionContext
    ) -> object:
        """Resolve a single parameter through provider chain."""
        for provider in self._providers:
            if provider.can_handle(param, context):
                return provider.resolve(param, context)

        # Fallback: return default if exists, otherwise None
        return None if param.default is inspect.Parameter.empty else param.default

    def register_dependency(
        self, name: str, callable_dep: Callable[..., Any]
    ) -> Callable[..., Any]:
        r"""Register a callable as a dependency by name.

        Inject with ``Depends("name")``.
        """
        self._dependency_callables[name] = callable_dep
        return callable_dep

    def dependency(
        self, name: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        r"""Return a decorator that registers a callable as a dependency.

        Example: @resolver.dependency("name").
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_dependency(name, func)
            return func

        return decorator

    def _resolve_callable_dependency(
        self, name: str, context: ResolutionContext
    ) -> object:
        """Resolve a registered dependency by name with caching and cycle detection."""
        if name not in self._dependency_callables:
            return None

        callable_dep = self._dependency_callables[name]

        # Check for cycles using stack
        if name in context.stack:
            cycle_start = context.stack.index(name)
            cycle = [*context.stack[cycle_start:], name]
            raise DependencyCycleError(cycle)

        # Check cache
        cached = context.cache.get(name)
        if cached is _IN_PROGRESS:
            raise DependencyCycleError([*context.stack, name])
        if cached is not _CACHE_MISS:
            return cached

        # Mark as in progress and resolve
        context.stack.append(name)
        context.cache.mark_in_progress(name)

        try:
            resolved = self.resolve(callable_dep, context)
            value = callable_dep(**resolved)
            context.cache.set(name, value)
            return value
        finally:
            if context.stack and context.stack[-1] == name:
                context.stack.pop()
            context.cache.unmark_in_progress(name)

    def add_provider(self, provider: ParameterProvider) -> None:
        """Append a provider to the resolution chain."""
        self._providers.append(provider)

    def register(
        self,
        provider: ParameterProvider | type[ParameterProvider],
    ) -> ParameterProvider | type[ParameterProvider]:
        """Register a provider (instance or class). Use as @resolver.register."""
        if isinstance(provider, type):
            instance = provider()
            self.add_provider(instance)
            return provider
        self.add_provider(provider)
        return provider

    def resolve(
        self, func: Callable[..., T], context: ResolutionContext
    ) -> dict[str, Any]:
        """Resolve function parameters from context."""
        self._ensure_providers()

        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            return {}

        result: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if self._should_skip_parameter(param):
                continue

            value = self._resolve_parameter(param, context)
            result[name] = value

        return result

    def resolve_dependencies(
        self, func: Callable[..., Any], **context: object
    ) -> dict[str, Any]:
        """Resolve arguments for func from context."""
        self._ensure_providers()

        # Convert legacy dict context to ResolutionContext
        reserved = self.EXPLICIT_RESOLVE_KEYS
        url_kwargs = {k: v for k, v in context.items() if k not in reserved}

        # Handle legacy dict-based cache
        cache_obj = context.get("_cache")
        if isinstance(cache_obj, dict):
            cache = DependencyCache(backing_dict=cache_obj)
        elif isinstance(cache_obj, DependencyCache):
            cache = cache_obj
        else:
            cache = DependencyCache()

        resolution_context = ResolutionContext(
            request=cast("Any", context.get("request")),
            form=context.get("form"),
            url_kwargs=url_kwargs,
            context_data=cast("Any", context.get("_context_data") or {}),
            cache=cache,
            stack=cast("list[str]", context.get("_stack") or []),
        )

        return self.resolve(func, resolution_context)

    def resolve_with_template_context(
        self,
        func: Callable[..., Any],
        *,
        request: HttpRequest | None = None,
        template_context: dict[str, Any] | None = None,
        _cache: dict[str, Any] | DependencyCache | None = None,
        _stack: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve func for component callables.

        Keys in EXPLICIT_RESOLVE_KEYS are omitted from _context_data so
        ContextByNameProvider does not beat HttpRequestProvider on a parameter
        named request.
        """
        tc: dict[str, Any] = dict(template_context or {})
        injectable = {
            k: v for k, v in tc.items() if k not in self.EXPLICIT_RESOLVE_KEYS
        }

        if isinstance(_cache, dict):
            cache = DependencyCache(backing_dict=_cache)
        elif isinstance(_cache, DependencyCache):
            cache = _cache
        else:
            cache = DependencyCache()

        context = ResolutionContext(
            request=request,
            form=tc.get("form"),
            url_kwargs={},
            context_data=injectable,
            cache=cache,
            stack=_stack or [],
        )

        return self.resolve(func, context)


class ParameterProvider(Protocol):
    """Protocol for parameter providers."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Check if this provider can supply a value for the parameter."""
        ...

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Resolve and return the value for the parameter."""
        ...


class ProviderRegistry:
    """Registry for parameter providers with explicit registration."""

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._providers: list[ParameterProvider] = []

    def register(self, provider: ParameterProvider) -> None:
        """Register a parameter provider."""
        self._providers.append(provider)

    def get_providers(self) -> tuple[ParameterProvider, ...]:
        """Get all registered providers as immutable tuple."""
        return tuple(self._providers)

    def clear(self) -> None:
        """Clear all registered providers."""
        self._providers.clear()

    def __len__(self) -> int:
        """Return number of registered providers."""
        return len(self._providers)

    def __iter__(self) -> Iterator[ParameterProvider]:
        """Iterate over registered providers."""
        return iter(self._providers)


resolver = DependencyResolver()


class RegisteredParameterProvider(ABC):
    """Base for parameter providers that auto-register via __init_subclass__."""

    resolver = resolver
    _registry: ClassVar[list[type]] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Register the subclass in _registry."""
        super().__init_subclass__(**kwargs)
        RegisteredParameterProvider._registry.append(cls)

    @abstractmethod
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Check if this provider can supply a value for the parameter."""

    @abstractmethod
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Resolve and return the value for the parameter."""


@dataclass(frozen=True, slots=True)
class Depends:
    """Mark a parameter as a dependency to be resolved by the resolver.

    Use as a default parameter value:

    - ``Depends("name")``: resolve a registered dependency by name
    - ``Depends(callable)``: call a dependency factory with DI-resolved args
    - ``Depends(value)``: inject a constant value
    - ``Depends()``: resolve by parameter name (shorthand for ``Depends("param_name")``)
    """

    dependency: object | None = None


class DependsProvider(RegisteredParameterProvider):
    """Resolve parameters declared with ``Depends(...)`` defaults."""

    def __init__(self, resolver: DependencyResolver) -> None:
        """Store resolver for resolving nested dependencies."""
        self._resolver = resolver

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Check if param.default is a Depends marker."""
        _: ResolutionContext = context
        return isinstance(param.default, Depends)

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Resolve Depends by name, callable, or constant value."""
        marker = param.default
        if not isinstance(marker, Depends):
            return None

        dep = marker.dependency
        if dep is None:
            dep = param.name

        if isinstance(dep, str):
            return self._resolver._resolve_callable_dependency(dep, context)

        if callable(dep):
            resolved = self._resolver.resolve(dep, context)
            return dep(**resolved)

        return dep
