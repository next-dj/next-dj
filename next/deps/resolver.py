"""Walk a callable's signature and fill its parameters from the context.

`DependencyResolver` is the orchestrator consumed by page views, form
actions, and component renderers. It instantiates every subclass
registered under `RegisteredParameterProvider._registry` on first use.
Providers register by simply importing their module, which runs the
`__init_subclass__` hook on `RegisteredParameterProvider`.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast

from .cache import _CACHE_MISS, _IN_PROGRESS, DependencyCache, DependencyCycleError
from .context import RESERVED_KEYS, ResolutionContext
from .providers import ParameterProvider, RegisteredParameterProvider


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest


T = TypeVar("T")


class DependencyResolver:
    """Build keyword-arguments for a callable by consulting registered providers."""

    EXPLICIT_RESOLVE_KEYS: ClassVar[frozenset[str]] = RESERVED_KEYS

    def __get__(self, obj: object, owner: type[object]) -> DependencyResolver:
        """Return the resolver itself when accessed as a descriptor."""
        return self

    def __init__(
        self, *providers: ParameterProvider | RegisteredParameterProvider
    ) -> None:
        """Initialise with explicit providers or defer to the auto-registry."""
        self._dependency_callables: dict[str, Callable[..., Any]] = {}
        self._providers: list[ParameterProvider] = list(providers)
        self._providers_loaded = bool(providers)
        self._resolve_call_stack: list[Callable[..., Any]] = []

    def _get_providers(self) -> list[ParameterProvider]:
        """Instantiate every registered `RegisteredParameterProvider` subclass."""
        result: list[ParameterProvider] = []
        for cls in RegisteredParameterProvider._registry:
            sig = inspect.signature(cls)
            if "resolver" in sig.parameters:
                result.append(cls(resolver=self))
            else:
                result.append(cls())
        return result

    def _ensure_providers(self) -> None:
        """Populate `_providers` from the auto-registry on first access."""
        if not self._providers_loaded:
            self._providers.extend(self._get_providers())
            self._providers_loaded = True

    def _should_skip_parameter(self, param: inspect.Parameter) -> bool:
        """Return True for `self` / `cls` and variadic parameters."""
        return param.name in ("self", "cls") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )

    def _resolve_parameter(
        self, param: inspect.Parameter, context: ResolutionContext
    ) -> object:
        """Return the first provider result, otherwise the default or None."""
        for provider in self._providers:
            if provider.can_handle(param, context):
                return provider.resolve(param, context)
        return None if param.default is inspect.Parameter.empty else param.default

    def register_dependency(
        self, name: str, callable_dep: Callable[..., Any]
    ) -> Callable[..., Any]:
        """Register a callable as a dependency reachable through `Depends("name")`."""
        self._dependency_callables[name] = callable_dep
        return callable_dep

    def dependency(
        self, name: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Return a decorator that registers the callable under the given name."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_dependency(name, func)
            return func

        return decorator

    def _resolve_callable_dependency(
        self, name: str, context: ResolutionContext
    ) -> object:
        """Resolve a named dependency with cycle-safe memoisation."""
        if name not in self._dependency_callables:
            return None

        callable_dep = self._dependency_callables[name]

        if name in context.stack:
            cycle_start = context.stack.index(name)
            cycle = [*context.stack[cycle_start:], name]
            raise DependencyCycleError(cycle)

        cached = context.cache.get(name)
        if cached is _IN_PROGRESS:
            raise DependencyCycleError([*context.stack, name])
        if cached is not _CACHE_MISS:
            return cached

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
        """Append a provider after the existing list."""
        self._providers.append(provider)

    def register(
        self,
        provider: ParameterProvider | type[ParameterProvider],
    ) -> ParameterProvider | type[ParameterProvider]:
        """Register a provider, accepting either a class or an instance."""
        if isinstance(provider, type):
            instance = provider()
            self.add_provider(instance)
            return provider
        self.add_provider(provider)
        return provider

    def resolve(
        self, func: Callable[..., T], context: ResolutionContext
    ) -> dict[str, Any]:
        """Return keyword arguments ready to call `func` with the given context."""
        self._ensure_providers()

        self._resolve_call_stack.append(func)
        try:
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
        finally:
            self._resolve_call_stack.pop()

    def resolve_dependencies(
        self, func: Callable[..., Any], **context: object
    ) -> dict[str, Any]:
        """Resolve `func` from a loose kwargs mapping and build a context object."""
        self._ensure_providers()

        reserved = self.EXPLICIT_RESOLVE_KEYS
        url_kwargs = {k: v for k, v in context.items() if k not in reserved}

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
        """Resolve `func` for component callables using template context.

        Keys from `EXPLICIT_RESOLVE_KEYS` are stripped from the context
        data so that name-based providers cannot shadow dedicated
        providers such as `HttpRequestProvider` on a parameter literally
        named `request`.
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


resolver: DependencyResolver = DependencyResolver()

RegisteredParameterProvider.resolver = resolver
