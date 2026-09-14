"""Fill a callable's parameters from the context by replaying its compiled plan.

`DependencyResolver` serves page views, form actions, and component renderers, and a
plan is compiled once per callable rather than scanned per resolve.
"""

from __future__ import annotations

import inspect
import logging
import threading
from operator import attrgetter
from typing import TYPE_CHECKING, Any, cast, override

from django.utils.functional import SimpleLazyObject, empty

from next.backends import resolve_setting_class
from next.caches import LruCache
from next.conf.signals import settings_reloaded
from next.introspect import describe_callable

from .cache import _CACHE_MISS, _IN_PROGRESS, DependencyCache
from .context import RESERVED_KEYS, ResolutionContext
from .errors import DependencyCycleError, UnknownDependencyError
from .introspect import (
    HINT_ERRORS,
    IntrospectKey,
    cached_signature,
    cached_type_hints,
    forget_introspection_caches,
    introspect_key,
)
from .plan import EMPTY_PLAN, InjectionPlan, compile_plan
from .providers import ParameterProvider, RegisteredParameterProvider
from .registry import _Address, _address, provider_registry


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from django.http import HttpRequest


logger = logging.getLogger(__name__)


# The names `resolve_dependencies` takes out one by one, spelled there for speed.
# Whatever `RESERVED_KEYS` grows beyond them is stripped by the loop that follows,
# so a new reserved name never reaches the URL kwargs a provider reads by name.
_CLAIMED_RESERVED = frozenset(
    {"_cache", "request", "form", "cleaned_data", "_context_data", "_stack"}
)
_UNCLAIMED_RESERVED: tuple[str, ...] = tuple(RESERVED_KEYS - _CLAIMED_RESERVED)


class _Described:
    """Log argument that describes its callable only once a handler formats it.

    A disabled debug log discards the record before formatting, so the walk
    `_describe_callable` runs costs nothing on the path that repeats it.
    """

    __slots__ = ("func",)

    def __init__(self, func: Callable[..., Any]) -> None:
        """Hold the callable the message names."""
        self.func = func

    @override
    def __str__(self) -> str:
        """Describe the callable for the formatted record."""
        return describe_callable(self.func)


def _adopt_provider[P](provider: P) -> P:
    """Return `provider` once the hooks the plan compiler calls hold up.

    Checked as the provider joins, so an outdated contract names itself here.
    """
    if not callable(getattr(provider, "static_can_handle", None)):
        msg = (
            f"{type(provider).__name__} defines no callable static_can_handle, "
            "which every parameter provider owes the plan compiler. Implement "
            "it to classify a parameter from its signature alone and return "
            "None for a verdict that depends on the context."
        )
        raise TypeError(msg)
    hook = getattr(provider, "compile_resolve", None)
    if hook is not None and not callable(hook):
        msg = (
            f"{type(provider).__name__} carries a compile_resolve that is not "
            "callable. The hook is optional, so leave it out to keep the plain "
            "resolve path, or make it return the call the plan holds."
        )
        raise TypeError(msg)
    return provider


def _as_cache(cache_obj: object) -> DependencyCache:
    """Return the cache a caller handed over, wrapping a plain dict in one.

    Callers spell out the common `None` case themselves, costing this five percent.
    """
    if isinstance(cache_obj, DependencyCache):
        return cache_obj
    if isinstance(cache_obj, dict):
        return DependencyCache(backing_dict=cache_obj)
    return DependencyCache()


def _drop[P](holder: list[P], provider: object) -> bool:
    """Remove every occurrence of `provider` from `holder`, by identity."""
    kept = [held for held in holder if held is not provider]
    if len(kept) == len(holder):
        return False
    holder[:] = kept
    return True


def forget_dep_caches(**kwargs) -> None:
    """Drop the introspection memos, so a reloaded module leaves none behind.

    A saved `page.py` mints fresh function objects the memos would otherwise pin
    along with their globals, and the per-name dependency bindings go with them.
    """
    forget_introspection_caches()
    resolver._plan_cache.clear()
    resolver._leaf_dependencies.clear()


class DependencyResolver:
    """Build keyword arguments for a callable by replaying its compiled plan.

    The plan cache entry is one tuple, swapped atomically under the GIL against races.
    """

    def __init__(
        self, *providers: ParameterProvider | RegisteredParameterProvider
    ) -> None:
        """Initialise with explicit providers or defer to the auto-registry."""
        self._dependency_callables: dict[str, Callable[..., Any]] = {}
        # Reentrant, because building a provider may resolve through this very
        # resolver and would otherwise deadlock against its own resync.
        self._lock = threading.RLock()
        self._head: list[ParameterProvider] = []
        self._auto: list[RegisteredParameterProvider] = []
        self._tail: list[ParameterProvider] = [_adopt_provider(p) for p in providers]
        self._providers: list[ParameterProvider] = list(self._tail)
        # Explicit providers make the registry irrelevant for good, so a sync
        # only records the registry version it would otherwise catch up to.
        self._explicit = bool(providers)
        self._registry_seen = -1
        # Addresses a `remove_provider` dropped, so a later resync does not put
        # the instance the caller took out back on the list. Addressed the way
        # the registry addresses a class, so a reloader re-executing the module
        # that declares it does not slip a fresh class past the removal.
        self._suppressed: set[_Address] = set()
        self._syncing = False
        # Bumped on every `_providers` mutation, so a compiled plan carries the
        # version it saw and is recompiled once the list has moved on.
        self._providers_version = 0
        self._plan_cache: LruCache[IntrospectKey, tuple[int, InjectionPlan]] = (
            LruCache()
        )
        # Dependency callables whose plan fills nothing, held under the name they answer
        # to and checked by identity, so a rebound name is looked at afresh.
        self._leaf_dependencies: dict[str, Callable[..., Any]] = {}

    def _instantiate(
        self, cls: type[RegisteredParameterProvider]
    ) -> RegisteredParameterProvider:
        """Build one auto-registered provider, handing over the resolver when asked."""
        if "resolver" in cached_signature(cls).parameters:
            return cast("RegisteredParameterProvider", cast("Any", cls)(resolver=self))
        return cls()

    def _rebuild(self) -> None:
        """Publish the flat provider list and move the version plans carry.

        A fresh list rather than an in-place edit, so a compile walking the
        previous one keeps a consistent snapshot for the whole walk.
        """
        self._providers = [*self._head, *self._auto, *self._tail]
        self._providers_version += 1

    def _sync_providers(self) -> None:
        """Rebuild the auto-registered instances whenever the registry has moved.

        Full resync, not a tail delta, since a replaced class or a race must land here.
        """
        if self._registry_seen == provider_registry.version:
            return
        if self._explicit:
            self._registry_seen = provider_registry.version
            return
        with self._lock:
            version = provider_registry.version
            if self._registry_seen == version or self._syncing:
                # A provider constructed through this resolver re-enters here, and the
                # half-built list is the wrong one to restart from.
                return
            self._syncing = True
            try:
                # An abstract intermediate base is a legitimate class to
                # register and no instance of it exists to place.
                auto = [
                    _adopt_provider(self._instantiate(cls))
                    for cls in provider_registry
                    if not inspect.isabstract(cls)
                    and _address(cls) not in self._suppressed
                ]
            finally:
                self._syncing = False
            auto.sort(key=attrgetter("priority"))
            self._auto = auto
            # The rebuild lands first, so a resolve on another thread never
            # finds the registry caught up while the plans it stamped are
            # still reading fresh against the previous provider list.
            self._rebuild()
            self._registry_seen = version

    def skips(self, param: inspect.Parameter) -> bool:
        """Return True for `self` / `cls` and variadic parameters.

        Public because `compile_plan` takes it as the predicate a plan is
        built from, and a subclass may widen what the resolver refuses.
        """
        return param.name in ("self", "cls") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )

    def _compile_plan(self, func: Callable[..., Any]) -> tuple[InjectionPlan, bool]:
        """Compile the plan for `func` and say whether it is settled enough to cache.

        Unresolved hints leave raw annotations in place and mark the plan provisional,
        so a name a later import defines is retried rather than frozen out for good.
        """
        try:
            sig = cached_signature(func)
        except (ValueError, TypeError):
            return EMPTY_PLAN, True
        try:
            hints = cached_type_hints(func)
        except HINT_ERRORS:
            logger.debug(
                "Type hints of %s did not resolve, so its plan reads the raw "
                "annotations and is compiled again on the next resolve.",
                _Described(func),
                exc_info=True,
            )
            return compile_plan(sig, {}, self._providers, self.skips), False
        return compile_plan(sig, hints, self._providers, self.skips), True

    def _plan_for(self, key: IntrospectKey, func: Callable[..., Any]) -> InjectionPlan:
        """Return the plan for `func` under `key`, recompiling once the providers moved.

        The version is read before the compile, so a plan built from a list
        another thread was replacing is stamped stale rather than fresh.
        """
        self._sync_providers()
        version = self._providers_version
        try:
            entry = self._plan_cache.get(key)
        except TypeError:
            # An unhashable callable compiles per resolve rather than losing injection.
            return self._compile_plan(func)[0]
        if entry is not None and entry[0] == version:
            return entry[1]
        plan, settled = self._compile_plan(func)
        if settled:
            self._plan_cache[key] = (version, plan)
        return plan

    def provides(
        self,
        func: Callable[..., Any],
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Return whether a registered provider fills `param` of `func` in `context`.

        The answer comes from the compiled plan, so a skipped parameter never matches
        and candidates see the resolved parameter `resolve` hands them.
        """
        plan = self._plan_for(introspect_key(func), func)
        entry = next((e for e in plan if e[0] == param.name), None)
        if entry is None:
            return False
        _name, candidates, _fallback, resolved, filler = entry
        if filler is not None:
            return True
        return any(provider.can_handle(resolved, context) for provider in candidates)

    def register_dependency(
        self, name: str, callable_dep: Callable[..., Any]
    ) -> Callable[..., Any]:
        """Register a callable as a dependency reachable through `Depends("name")`.

        Any note the name carries goes with it, so the new callable is looked at afresh
        and no callable of an earlier generation stays pinned here.
        """
        self._dependency_callables[name] = callable_dep
        self._leaf_dependencies.pop(name, None)
        return callable_dep

    def get_dependency(self, name: str) -> Callable[..., Any] | None:
        """Return the callable bound to `name`, or None when nothing is bound."""
        return self._dependency_callables.get(name)

    def unregister_dependency(self, name: str) -> None:
        """Drop the binding for `name`, tolerating a name that has none."""
        self._dependency_callables.pop(name, None)
        self._leaf_dependencies.pop(name, None)

    def dependency(
        self, name: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Return a decorator that registers the callable under the given name."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_dependency(name, func)
            return func

        return decorator

    def _resolve_callable_dependency(
        self,
        name: str,
        context: ResolutionContext,
        *,
        param: inspect.Parameter | None = None,
    ) -> object:
        """Resolve a named dependency with cycle-safe memoisation.

        An unregistered name raises at once rather than injecting `None`, so the
        error surfaces at the `Depends` site instead of deep inside user code.
        """
        callable_dep = self._dependency_callables.get(name)
        if callable_dep is None:
            raise UnknownDependencyError(
                name,
                None if param is None else param.name,
                registered=tuple(self._dependency_callables),
            )

        if name in context.stack:
            cycle_start = context.stack.index(name)
            cycle = [*context.stack[cycle_start:], name]
            raise DependencyCycleError(cycle)

        cached = context.cache.get(name)
        if cached is _IN_PROGRESS:
            raise DependencyCycleError([*context.stack, name])
        if cached is not _CACHE_MISS:
            return cached

        if self._leaf_dependencies.get(name) is callable_dep:
            # Nothing to inject is nothing that could re-enter this resolution, so the
            # call needs neither the stack nor the in-progress marker.
            value = callable_dep()
            context.cache.set(name, value)
            return value

        context.stack.append(name)
        context.cache.mark_in_progress(name)

        try:
            resolved = self.resolve(callable_dep, context)
            if not resolved:
                # No provider can add a parameter to a signature, so a plan that came
                # back empty stays empty for as long as the name keeps this callable.
                self._leaf_dependencies[name] = callable_dep
            value = callable_dep(**resolved)
            context.cache.set(name, value)
            return value
        finally:
            # Every nested resolution pops what it pushed, so this name is on top.
            context.stack.pop()
            context.cache.unmark_in_progress(name)

    def add_provider(self, provider: ParameterProvider) -> None:
        """Append a provider after the existing list."""
        with self._lock:
            self._tail.append(_adopt_provider(provider))
            self._rebuild()

    def prepend_provider(self, provider: ParameterProvider) -> None:
        """Put `provider` ahead of every other one, so it matches first."""
        with self._lock:
            self._head.insert(0, _adopt_provider(provider))
            self._rebuild()

    def remove_provider(self, provider: ParameterProvider) -> None:
        """Drop `provider` by identity, tolerating one the resolver never held.

        Identity beats equality, since two dataclass providers compare equal, and
        suppression keeps a resync from restoring an auto one.
        """
        with self._lock:
            dropped = _drop(self._head, provider)
            if _drop(self._tail, provider):
                dropped = True
            if _drop(self._auto, provider):
                self._suppressed.add(_address(type(provider)))
                dropped = True
            if dropped:
                self._rebuild()

    def register(
        self, provider: ParameterProvider | type[ParameterProvider]
    ) -> ParameterProvider | type[ParameterProvider]:
        """Register a provider, accepting either a class or an instance."""
        if isinstance(provider, type):
            self.add_provider(provider())
            return provider
        self.add_provider(provider)
        return provider

    def resolve[T](
        self, func: Callable[..., T], context: ResolutionContext
    ) -> dict[str, Any]:
        """Return keyword arguments for `func` by replaying its compiled plan.

        A missing dependency names the callable filled here, the innermost of a nested
        chain and so the one whose signature carries the offending `Depends`.
        """
        # The hit path is inlined because a miss is rare enough to afford the helper.
        key = introspect_key(func)
        try:
            entry = self._plan_cache.get(key)
        except TypeError:
            entry = None
        if (
            entry is None
            or entry[0] != self._providers_version
            or self._registry_seen != provider_registry.version
        ):
            plan = self._plan_for(key, func)
        else:
            plan = entry[1]
        if not plan:
            return {}

        result: dict[str, Any] = {}
        try:
            for name, candidates, fallback, param, filler in plan:
                for provider in candidates:
                    if provider.can_handle(param, context):
                        result[name] = provider.resolve(param, context)
                        break
                else:
                    result[name] = fallback if filler is None else filler(context)
        except UnknownDependencyError as exc:
            exc.attribute_to(func)
            raise
        return result

    def resolve_dependencies(
        self, func: Callable[..., Any], **context
    ) -> dict[str, Any]:
        """Resolve `func` from a loose kwargs mapping and build a context object.

        The reserved names are popped from the mapping rather than filtered into a copy,
        because `**context` owns its dict and what is left of it is the URL kwargs.
        """
        cache_obj = context.pop("_cache", None)
        request = context.pop("request", None)
        form = context.pop("form", None)
        cleaned_data = context.pop("cleaned_data", None)
        context_data = context.pop("_context_data", None) or {}
        stack = context.pop("_stack", None) or []
        for reserved in _UNCLAIMED_RESERVED:
            context.pop(reserved, None)

        resolution_context = ResolutionContext(
            request=request,
            form=form,
            url_kwargs=context,
            context_data=context_data,
            cache=DependencyCache() if cache_obj is None else _as_cache(cache_obj),
            stack=stack,
            cleaned_data=cleaned_data,
        )

        return self.resolve(func, resolution_context)

    def resolve_with_template_context(
        self,
        func: Callable[..., Any],
        *,
        request: HttpRequest | None = None,
        template_context: Mapping[str, Any] | None = None,
        _cache: dict[str, Any] | DependencyCache | None = None,
        _stack: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve `func` for component callables using template context.

        The context travels unfiltered, since providers reading it by name already
        refuse `RESERVED_KEYS` themselves, cheaper than a full copy.
        """
        cache = DependencyCache() if _cache is None else _as_cache(_cache)

        context = ResolutionContext(
            request=request,
            form=None if template_context is None else template_context.get("form"),
            url_kwargs={},
            context_data={} if template_context is None else template_context,
            cache=cache,
            stack=_stack or [],
        )

        return self.resolve(func, context)


# One holder every reference points at, with the configured resolver behind it.
# The object is built afresh whenever the setting names another class, so the
# holder is what keeps a framework module, a provider and a test helper reading
# the resolver in force rather than the one they imported.
_holder = SimpleLazyObject(DependencyResolver)
resolver: DependencyResolver = cast("DependencyResolver", _holder)

RegisteredParameterProvider.resolver = resolver


def _configured_resolver_class() -> type[DependencyResolver]:
    """Return the class named by `NEXT_FRAMEWORK["DEPENDENCY_RESOLVER"]`."""
    return resolve_setting_class(
        "DEPENDENCY_RESOLVER",
        base=DependencyResolver,
        # The package binds `DependencyResolver` only after importing this module,
        # so the import helper would hit a half-initialised `next.deps`.
        shipped=DependencyResolver,
        base_path="next.deps.DependencyResolver",
    )


def current_resolver() -> DependencyResolver:
    """Return the resolver object the holder stands for, building it on first read.

    Read on a render path, because an attribute off the holder is a forwarded call.
    """
    if _holder._wrapped is empty:
        _holder._setup()
    built: DependencyResolver = _holder._wrapped
    return built


def apply_resolver_setting() -> None:
    """Put an instance of the configured resolver class behind the holder.

    A fresh object rather than a retype, so the `__init__` of a subclass runs and
    everything the resolver it replaces was told at runtime goes with that object.
    """
    cls = _configured_resolver_class()
    if _holder._wrapped is not empty and type(_holder._wrapped) is cls:
        return
    _holder._wrapped = cls()


def _on_settings_reloaded(**kwargs) -> None:
    """Rebuild the resolver singleton when the framework settings change."""
    apply_resolver_setting()


settings_reloaded.connect(_on_settings_reloaded)
