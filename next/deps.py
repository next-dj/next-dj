"""Dependency resolution for core providers.

Resolves function parameters from request context (request, URL kwargs, form)
by inspecting signatures and matching parameters to known sources. Enables
FastAPI-style dependency injection so callables declare only the arguments
they need.
"""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, get_args, get_origin

from django.http import HttpRequest


# Sentinel for "dependency currently being resolved" to detect cycles
_IN_PROGRESS: object = object()


class DependencyCycleError(Exception):
    """Raised when a circular dependency is detected between registered callables."""

    def __init__(self, cycle: list[str]) -> None:
        """Store cycle path and set exception message."""
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


@dataclass
class RequestContext:
    """Context for resolving dependencies within a single request."""

    request: HttpRequest | None = None
    form: object = None
    url_kwargs: dict[str, Any] = field(default_factory=dict)
    # Accumulated context from previously run context functions (key -> value)
    context_data: dict[str, Any] = field(default_factory=dict)
    # Used by CallableDependencyProvider for request-scoped cache and cycle detection
    cache: dict[str, Any] | None = None
    resolver: "Deps | None" = None
    stack: list[str] | None = None  # names being resolved (cycle detection)


class ParameterProvider(Protocol):
    """Protocol for resolving a single parameter from request context."""

    def can_handle(self, param: inspect.Parameter, context: RequestContext) -> bool:
        """Return True if this provider can supply a value for the parameter."""

    def resolve(self, param: inspect.Parameter, context: RequestContext) -> object:
        """Return the value for the parameter. Called only when can_handle is True."""


class HttpRequestProvider:
    """Provides HttpRequest when parameter is annotated with HttpRequest or subclass."""

    def can_handle(self, param: inspect.Parameter, context: RequestContext) -> bool:
        """Return True if param is HttpRequest-annotated and request in context."""
        if context.request is None:
            return False
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            return False
        return isinstance(ann, type) and issubclass(ann, HttpRequest)

    def resolve(self, _param: inspect.Parameter, context: RequestContext) -> object:
        """Return the request from context."""
        return context.request


class UrlKwargsProvider:
    """Provides URL path parameter values by parameter name."""

    def can_handle(self, param: inspect.Parameter, context: RequestContext) -> bool:
        """Return True if param name is in context url_kwargs."""
        return param.name in context.url_kwargs

    def resolve(self, param: inspect.Parameter, context: RequestContext) -> object:
        """Return value from url_kwargs with type coercion (int, list[str] for path)."""
        value = context.url_kwargs[param.name]
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            return value
        if ann is int and not isinstance(value, int) and isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                pass
        if get_origin(ann) is list:
            args_tuple = get_args(ann)
            if args_tuple and args_tuple[0] is str and isinstance(value, str):
                return [s for s in value.split("/") if s]
        return value


class FormProvider:
    """Provide form instance when param is form class or named 'form'."""

    def can_handle(self, param: inspect.Parameter, context: RequestContext) -> bool:
        """Return True if param is form or annotated with form class in context."""
        if context.form is None:
            return False
        if param.name == "form":
            return True
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            return False
        return isinstance(ann, type) and isinstance(context.form, ann)

    def resolve(self, _param: inspect.Parameter, context: RequestContext) -> object:
        """Return the form instance from context."""
        return context.form


class ContextKeyProvider:
    """Provides parameter from accumulated context_data (e.g. from other context funcs).

    When resolving a context function, param.name is supplied from context_data
    if that key exists. Allows one @context("key") to be used as a dependency
    in another context function without registering a separate resolver.dependency.
    """

    def can_handle(self, param: inspect.Parameter, context: RequestContext) -> bool:
        """Return True if param.name is in context.context_data."""
        return param.name in context.context_data

    def resolve(self, param: inspect.Parameter, context: RequestContext) -> object:
        """Return the value from context_data for this param name."""
        return context.context_data[param.name]


DEFAULT_PROVIDERS: list[ParameterProvider] = [
    HttpRequestProvider(),
    ContextKeyProvider(),  # before URL kwargs so context keys win in context functions
    UrlKwargsProvider(),
    FormProvider(),
]


class CallableDependencyProvider:
    """Provides parameter values by calling registered dependency callables.

    Used when a parameter name matches a name registered via
    resolver.register_dependency(name, callable). The callable is invoked with
    its own resolved dependencies; result is cached per request when _cache
    is passed to resolve_dependencies.
    """

    def __init__(self, resolver: "Deps") -> None:
        """Store reference to the Deps instance that holds the dependency registry."""
        self._resolver = resolver

    def can_handle(self, param: inspect.Parameter, _context: RequestContext) -> bool:
        """Return True if param name is registered as a dependency callable."""
        return param.name in self._resolver._dependency_callables

    def resolve(self, param: inspect.Parameter, context: RequestContext) -> object:
        """Resolve by calling the registered callable; use cache and detect cycles."""
        name = param.name
        callable_dep = self._resolver._dependency_callables[name]
        cache = context.cache
        resolver = context.resolver
        # Cycle: if this name is already on the resolution stack, we have a cycle
        if context.stack is not None and name in context.stack:
            cycle = [*context.stack[context.stack.index(name) :], name]
            raise DependencyCycleError(cycle)
        if cache is not None and context.stack is not None and resolver is not None:
            if name in cache:
                value = cache[name]
                if value is _IN_PROGRESS:
                    cycle = [*context.stack, name]
                    raise DependencyCycleError(cycle)
                return value
            context.stack.append(name)
            cache[name] = _IN_PROGRESS
            try:
                inner_ctx = {
                    "request": context.request,
                    "form": context.form,
                    **context.url_kwargs,
                    "_cache": cache,
                    "_stack": context.stack,
                }
                resolved = resolver.resolve_dependencies(callable_dep, **inner_ctx)
                value = callable_dep(**resolved)
                cache[name] = value
                return value
            finally:
                if context.stack and context.stack[-1] == name:
                    context.stack.pop()
                if cache.get(name) is _IN_PROGRESS:
                    del cache[name]
        # No cache: resolve and call without caching (no cycle detection)
        if resolver is None:
            return None
        ctx_dict = {
            "request": context.request,
            "form": context.form,
            **context.url_kwargs,
        }
        resolved = resolver.resolve_dependencies(callable_dep, **ctx_dict)
        return callable_dep(**resolved)


class DependencyResolver(ABC):
    """Abstract resolver that builds call kwargs from request context."""

    @abstractmethod
    def resolve_dependencies(
        self, func: Callable[..., Any], **context: object
    ) -> dict[str, Any]:
        """Resolve arguments for func from context; return dict of name -> value."""
        ...


class Deps(DependencyResolver):
    """Resolves dependencies using providers passed at construction."""

    def __init__(self, *providers: ParameterProvider) -> None:
        """Initialize with providers (e.g. Deps(*DEFAULT_PROVIDERS) or Deps(p1, p2))."""
        self._providers = list(providers)
        self._dependency_callables: dict[str, Callable[..., Any]] = {}
        self._callable_provider: CallableDependencyProvider | None = None

    def register_dependency(
        self, name: str, callable_dep: Callable[..., Any]
    ) -> Callable[..., Any]:
        """Register a callable as a dependency by name (context/form/render)."""
        self._dependency_callables[name] = callable_dep
        if self._callable_provider is None:
            self._callable_provider = CallableDependencyProvider(self)
            self.add_provider(self._callable_provider)
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

    def add_provider(self, provider: ParameterProvider) -> None:
        """Append a provider; registered providers run after built-in ones."""
        self._providers.append(provider)

    def register(
        self, provider: ParameterProvider | type[ParameterProvider]
    ) -> ParameterProvider | type[ParameterProvider]:
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
        _cache = context.get("_cache")
        _stack = context.get("_stack")
        stack = _stack if _stack is not None else ([] if _cache else None)
        _context_data = context.get("_context_data")
        url_kwargs = {
            k: v
            for k, v in context.items()
            if k not in ("request", "form", "_cache", "_stack", "_context_data")
        }
        context_data = cast(
            "dict[str, Any]",
            _context_data if _context_data is not None else {},
        )
        req_context = RequestContext(
            request=cast("HttpRequest | None", context.get("request")),
            form=context.get("form"),
            url_kwargs=url_kwargs,
            context_data=context_data,
            cache=cast("dict[str, Any] | None", _cache),
            resolver=self,
            stack=cast("list[str] | None", stack),
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
                if provider.can_handle(param, req_context):
                    result[name] = provider.resolve(param, req_context)
                    break
            else:
                if param.default is inspect.Parameter.empty:
                    result[name] = None
        return result


DefaultDependencyResolver = Deps

resolver: Deps = Deps(*DEFAULT_PROVIDERS)
