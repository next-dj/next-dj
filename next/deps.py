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
import types
from abc import ABC, abstractmethod
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from typing import Any, ClassVar


class DDependencyBase[T]:
    """Base for all D-markers.

    All DI markers (e.g. DForm, DUrl) must inherit from this.
    """

    __slots__ = ()


# Sentinel for "dependency currently being resolved" to detect cycles
_IN_PROGRESS: object = object()


class DependencyCycleError(Exception):
    """Raised when a circular dependency is detected between registered callables."""

    def __init__(self, cycle: list[str]) -> None:
        """Store cycle path and set exception message."""
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


class DependencyResolver:
    """Resolves dependencies using auto-registered providers. Single global instance."""

    def __get__(self, obj: object, owner: type[object]) -> DependencyResolver:
        """Return self when used as class attribute on RegisteredParameterProvider."""
        return self

    def __init__(self, *providers: RegisteredParameterProvider) -> None:
        """Initialize with optional providers.

        With no args, chain is built lazily from registry on first resolve.
        """
        self._dependency_callables: dict[str, Callable[..., Any]] = {}
        self._providers: list[RegisteredParameterProvider] = list(providers)
        self._providers_loaded = bool(providers)

    def _get_providers(self) -> list[RegisteredParameterProvider]:
        """Build provider list from registry; inject resolver where needed."""
        import next.forms  # noqa: PLC0415
        import next.pages  # noqa: PLC0415
        import next.urls  # noqa: F401, PLC0415

        result: list[RegisteredParameterProvider] = []
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

    def _resolve_callable_dependency(self, name: str, context: object) -> object:
        """Resolve a registered dependency by name; uses cache and cycle detection."""
        if name not in self._dependency_callables:
            return None
        callable_dep = self._dependency_callables[name]
        cache = getattr(context, "cache", None)
        stack = getattr(context, "stack", None)
        if stack is not None and name in stack:
            cycle = [*stack[stack.index(name) :], name]
            raise DependencyCycleError(cycle)
        if cache is not None and stack is not None:
            if name in cache:
                value = cache[name]
                if value is _IN_PROGRESS:
                    raise DependencyCycleError([*stack, name])
                return value
            stack.append(name)
            cache[name] = _IN_PROGRESS
            try:
                inner_ctx: dict[str, object] = {
                    "request": getattr(context, "request", None),
                    "form": getattr(context, "form", None),
                    **getattr(context, "url_kwargs", {}),
                    "_cache": cache,
                    "_stack": stack,
                    "_context_data": getattr(context, "context_data", {}),
                }
                resolved = self.resolve_dependencies(callable_dep, **inner_ctx)
                value = callable_dep(**resolved)
                cache[name] = value
                return value
            finally:
                if stack and stack[-1] == name:
                    stack.pop()
                if cache.get(name) is _IN_PROGRESS:
                    del cache[name]
        inner_ctx = {
            "request": getattr(context, "request", None),
            "form": getattr(context, "form", None),
            **getattr(context, "url_kwargs", {}),
            "_context_data": getattr(context, "context_data", {}),
        }
        resolved = self.resolve_dependencies(callable_dep, **inner_ctx)
        return callable_dep(**resolved)

    def add_provider(self, provider: RegisteredParameterProvider) -> None:
        """Append a provider."""
        self._providers.append(provider)

    def register(
        self,
        provider: RegisteredParameterProvider | type[RegisteredParameterProvider],
    ) -> RegisteredParameterProvider | type[RegisteredParameterProvider]:
        """Register a provider (instance or class). Use as @resolver.register."""
        if isinstance(provider, type):
            instance = provider()
            self.add_provider(instance)
            return provider
        self.add_provider(provider)
        return provider

    def resolve_dependencies(
        self, func: Callable[..., Any], **context: object
    ) -> dict[str, Any]:
        """Resolve arguments for func from context; return dict of name -> value."""
        self._ensure_providers()
        reserved = {"request", "form", "_cache", "_stack", "_context_data"}
        url_kwargs = {k: v for k, v in context.items() if k not in reserved}
        ctx = types.SimpleNamespace(
            request=context.get("request"),
            form=context.get("form"),
            url_kwargs=url_kwargs,
            context_data=context.get("_context_data") or {},
            cache=context.get("_cache"),
            stack=context.get("_stack"),
            resolver=self,
        )
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            return {}
        result: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            for provider in self._providers:
                if provider.can_handle(param, ctx):
                    result[name] = provider.resolve(param, ctx)
                    break
            else:
                if param.default is inspect.Parameter.empty:
                    result[name] = None
        return result


resolver = DependencyResolver()


class RegisteredParameterProvider(ABC):
    """Base for parameter providers that auto-register via __init_subclass__.

    Any subclass is appended to _registry and will be included when building
    the default resolver chain. Use self.resolver to access the global resolver
    (e.g. for _resolve_callable_dependency). If the subclass __init__ accepts
    a 'resolver' parameter, DependencyResolver will pass self when instantiating.
    """

    resolver = resolver
    _registry: ClassVar[list[type]] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Register the subclass in _registry."""
        super().__init_subclass__(**kwargs)
        RegisteredParameterProvider._registry.append(cls)

    @abstractmethod
    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True if this provider can supply a value for the parameter."""

    @abstractmethod
    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Return the value for the parameter. Called only when can_handle is True."""


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

    def can_handle(self, param: inspect.Parameter, _context: object) -> bool:
        """Return True when param.default is a Depends marker."""
        return isinstance(param.default, Depends)

    def resolve(self, param: inspect.Parameter, context: object) -> object:
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
            inner_ctx: dict[str, object] = {
                "request": getattr(context, "request", None),
                "form": getattr(context, "form", None),
                **(getattr(context, "url_kwargs", {}) or {}),
                "_cache": getattr(context, "cache", None),
                "_stack": getattr(context, "stack", None),
                "_context_data": getattr(context, "context_data", {}) or {},
            }
            resolved = self._resolver.resolve_dependencies(dep, **inner_ctx)
            return dep(**resolved)
        return dep
