"""Dependency-injection helpers for tests.

Unit-testing a next-dj provider or a DI-aware callable usually means
building a `ResolutionContext` with sensible defaults and delegating to
the module-level `resolver`. These helpers wrap that ritual.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from next.deps.cache import DependencyCache
from next.deps.context import ResolutionContext
from next.deps.resolver import resolver


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from django.http import HttpRequest


def make_resolution_context(
    *,
    request: HttpRequest | None = None,
    form: object | None = None,
    url_kwargs: Mapping[str, Any] | None = None,
    context_data: Mapping[str, Any] | None = None,
    cleaned_data: Mapping[str, Any] | None = None,
    cache: DependencyCache | None = None,
    stack: list[str] | None = None,
) -> ResolutionContext:
    """Construct a `ResolutionContext` with empty defaults.

    A fresh `DependencyCache` and a fresh stack are created on every call so
    tests do not share memoised values or a resolution chain by accident. A
    test that wants to read either afterwards passes its own.
    """
    return ResolutionContext(
        request=request,
        form=form,
        url_kwargs=dict(url_kwargs or {}),
        context_data=dict(context_data or {}),
        cache=DependencyCache() if cache is None else cache,
        stack=[] if stack is None else stack,
        cleaned_data=cleaned_data,
    )


def resolve_call(
    func: Callable[..., Any],
    *,
    request: HttpRequest | None = None,
    form: object | None = None,
    url_kwargs: Mapping[str, Any] | None = None,
    context_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the dependencies of `func` and return the kwargs mapping.

    Thin wrapper over `DependencyResolver.resolve` that builds the context
    from the request, form, URL kwargs, and context data it is handed.
    """
    context = make_resolution_context(
        request=request, form=form, url_kwargs=url_kwargs, context_data=context_data
    )
    return resolver.resolve(func, context)


__all__ = ["make_resolution_context", "resolve_call"]
