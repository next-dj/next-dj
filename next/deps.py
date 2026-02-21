"""Dependency resolution for core providers.

Resolves function parameters from request context (request, URL kwargs, form)
by inspecting signatures and matching parameters to known sources. Enables
FastAPI-style dependency injection so callables declare only the arguments
they need.
"""

__all__ = [
    "DEFAULT_PROVIDERS",
    "DefaultDependencyResolver",
    "DependencyResolver",
    "Deps",
    "FormProvider",
    "HttpRequestProvider",
    "ParameterProvider",
    "RequestContext",
    "UrlKwargsProvider",
    "resolver",
]

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, get_args, get_origin

from django.http import HttpRequest


@dataclass
class RequestContext:
    """Context for resolving dependencies within a single request."""

    request: HttpRequest | None = None
    form: object = None
    url_kwargs: dict[str, Any] = field(default_factory=dict)


class ParameterProvider(Protocol):
    """Protocol for resolving a single parameter from request context."""

    def can_handle(self, param: inspect.Parameter, context: RequestContext) -> bool:
        """Return True if this provider can supply a value for the parameter."""
        ...

    def resolve(self, param: inspect.Parameter, context: RequestContext) -> object:
        """Return the value for the parameter. Called only when can_handle is True."""
        ...


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


DEFAULT_PROVIDERS: list[ParameterProvider] = [
    HttpRequestProvider(),
    UrlKwargsProvider(),
    FormProvider(),
]


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
        url_kwargs = {k: v for k, v in context.items() if k not in ("request", "form")}
        req_context = RequestContext(
            request=cast("HttpRequest | None", context.get("request")),
            form=context.get("form"),
            url_kwargs=url_kwargs,
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
