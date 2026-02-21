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


class DependencyResolver(ABC):
    """Abstract resolver that builds call kwargs from request context."""

    @abstractmethod
    def resolve_dependencies(
        self, func: Callable[..., Any], **context: object
    ) -> dict[str, Any]:
        """Resolve arguments for func from context; return dict of name -> value."""
        ...


class DefaultDependencyResolver(DependencyResolver):
    """Resolves dependencies using a list of parameter providers."""

    def __init__(
        self,
        providers: list[ParameterProvider] | None = None,
    ) -> None:
        """Initialize with optional list of providers; default set used if None."""
        if providers is None:
            providers = [
                HttpRequestProvider(),
                UrlKwargsProvider(),
                FormProvider(),
            ]
        self._providers = providers

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


_default_resolver = DefaultDependencyResolver()


def resolve_dependencies(
    func: Callable[..., Any],
    *,
    request: HttpRequest | None = None,
    form: object = None,
    **url_kwargs: object,
) -> dict[str, Any]:
    """Resolve callable arguments from request context.

    Convenience facade that builds RequestContext and uses DefaultDependencyResolver.
    """
    return _default_resolver.resolve_dependencies(
        func,
        request=request,
        form=form,
        **url_kwargs,
    )
