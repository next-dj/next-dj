"""Fill a callable's parameters from the context by replaying its compiled plan."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Coroutine
from contextvars import ContextVar, Token
from dataclasses import replace
from functools import partial
from operator import attrgetter
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, cast, overload, override

from django.utils.functional import SimpleLazyObject, empty

from next.backends import resolve_setting_class
from next.caches import ROUTES, LruCache, instance_dict
from next.introspect import describe_callable, is_async_callable, is_coroutine_callable

from .cache import _CACHE_MISS, _IN_PROGRESS, DependencyCache, request_memo
from .context import RECEIVER_NAMES, RESERVED_KEYS, ResolutionContext
from .errors import AsyncDependencyError, DependencyCycleError, UnknownDependencyError
from .introspect import (
    HINT_ERRORS,
    IntrospectKey,
    cached_signature,
    cached_type_hints,
    forget_introspection_caches,
    introspect_key,
)
from .plan import (
    EMPTY_PLAN,
    AsyncPlan,
    Blocking,
    Colour,
    InjectionPlan,
    compile_async_plan,
    compile_plan,
    edge_deferred,
    empty_async_plan,
    replay,
)
from .providers import ParameterProvider, RegisteredParameterProvider
from .registry import _Address, _address, provider_registry
from .runner import bridge, run_isolated


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from django.http import HttpRequest

    from .plan import AsyncParameterFiller, AwaitedEntry, Source


logger = logging.getLogger(__name__)


# Names `resolve_dependencies` pops inline for speed, the rest are stripped in a loop.
_CLAIMED_RESERVED = frozenset(
    {"_cache", "request", "form", "cleaned_data", "_context_data", "_stack"}
)
_UNCLAIMED_RESERVED: tuple[str, ...] = tuple(RESERVED_KEYS - _CLAIMED_RESERVED)

_compiling: ContextVar[tuple[tuple[str, bool], ...]] = ContextVar(
    "next_deps_compiling", default=()
)
_shares: ContextVar[frozenset[str]] = ContextVar(
    "next_deps_shares", default=frozenset()
)


class _Described:
    """Log argument that describes its callable only once a handler formats it.

    A disabled debug log discards the record before formatting, so the walk is free.
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


def _branch(context: ResolutionContext) -> ResolutionContext:
    """Return `context` with a stack of its own, for one of several concurrent entries.

    Entries sharing one list would pop each other's names off it.
    """
    return replace(context, stack=list(context.stack))


async def _awaited(fill: AsyncParameterFiller, context: ResolutionContext) -> object:
    """Await one filler inside a coroutine, the shape a task is created from."""
    return await fill(context)


async def _stop(tasks: list[asyncio.Task[object]]) -> None:
    """Cancel the tasks still running and wait until each of them has ended."""
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)


def _failures(tasks: list[asyncio.Task[object]]) -> list[BaseException]:
    """Return the error of every task that failed, in entry order."""
    return [
        error
        for task in tasks
        if not task.cancelled() and (error := task.exception()) is not None
    ]


def _log_failures(failures: list[BaseException]) -> None:
    """Log at debug level the failures of a group that another error stands for."""
    for error in failures:
        logger.debug(
            "A dependency of an async group failed beside the error the group raised.",
            exc_info=error,
        )


async def _await_group(
    entries: tuple[AwaitedEntry, ...],
    context: ResolutionContext,
    result: dict[str, Any],
) -> None:
    """Fill a group of async entries, concurrently once there are two or more.

    The first failure stops its siblings and the earliest entry's error is raised alone.
    """
    if len(entries) == 1:
        ((name, fill),) = entries
        result[name] = await fill(context)
        return
    tasks = [
        asyncio.create_task(_awaited(fill, _branch(context))) for _name, fill in entries
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except asyncio.CancelledError:
        await _stop(tasks)
        _log_failures(_failures(tasks))
        raise
    await _stop(tasks)
    failures = _failures(tasks)
    if failures:
        _log_failures(failures[1:])
        raise failures[0]
    for (name, _fill), task in zip(entries, tasks, strict=True):
        result[name] = task.result()


def _awaitable(func: Callable[..., Any], name: str | None = None) -> bool:
    """Return whether `func` is a coroutine function, refusing an async generator."""
    if not is_async_callable(func):
        return False
    if not is_coroutine_callable(func):
        raise AsyncDependencyError(func, name=name)
    return True


def _enter_shares(plan: AsyncPlan) -> Token[frozenset[str]] | None:
    """Add the keys `plan` shares to the current ones, None when none is new."""
    shared = plan.shared
    if not shared:
        return None
    current = _shares.get()
    if shared <= current:
        return None
    return _shares.set(current | shared)


def _fill_segment(
    entries: InjectionPlan, context: ResolutionContext
) -> tuple[object, ...]:
    """Fill a blocking segment in signature order and hand its values back whole."""
    return tuple([replay(entry, context) for entry in entries])


def _finish_call(
    entries: InjectionPlan,
    filled: Mapping[str, Any],
    target: Callable[..., object],
    context: ResolutionContext,
) -> object:
    """Fill the last blocking segment, then call the sync target with every argument."""
    kwargs = dict(filled)
    for entry in entries:
        kwargs[entry[0]] = replay(entry, context)
    return target(**kwargs)


_NO_SEGMENT = Blocking(())


def sharing() -> bool:
    """Return whether the request memo of this context shares any key at all."""
    memo = request_memo.get()
    if memo is None:
        return False
    return memo.share_all or bool(memo.shared) or bool(_shares.get())


def _shares_any(keys: frozenset[str]) -> bool:
    """Return whether the request memo of this context shares one of `keys`."""
    memo = request_memo.get()
    if memo is None:
        return False
    return (
        memo.share_all
        or not keys.isdisjoint(memo.shared)
        or not keys.isdisjoint(_shares.get())
    )


async def _seeded[T](
    resolver: DependencyResolver,
    sources: tuple[Source, ...],
    fill: Callable[[ResolutionContext], T],
    context: ResolutionContext,
) -> T:
    """Resolve the shared keys of `sources` on the loop, then run `fill` after them."""
    await resolver.aseed_sources(sources, context)
    return await run_isolated(fill, context)


def _segment[T](
    resolver: DependencyResolver,
    step: Blocking,
    fill: Callable[[ResolutionContext], T],
    context: ResolutionContext,
) -> Awaitable[T]:
    """Run `fill` as the segment of `step`, once the shared keys it reaches resolve.

    A segment reaching no shared key costs no coroutine beyond the runner's own.
    """
    keys = step.keys
    if keys and _shares_any(keys):
        return _seeded(resolver, step.sources, fill, context)
    return run_isolated(fill, context)


async def _areplay(
    resolver: DependencyResolver,
    plan: AsyncPlan,
    context: ResolutionContext,
    target: Callable[..., object] | None,
) -> tuple[dict[str, Any], object]:
    """Replay `plan`, calling a sync `target` inside its last blocking segment.

    A segment fills a private cache copy merged back once it returns, never in part.
    """
    result: dict[str, Any] = {}
    for entry in plan.pure:
        result[entry[0]] = replay(entry, context)
    steps = plan.steps
    tail = _NO_SEGMENT
    if target is not None and steps:
        last = steps[-1]
        if isinstance(last, Blocking):
            tail = last
            steps = steps[:-1]
    for step in steps:
        if isinstance(step, Blocking):
            fill = partial(_fill_segment, step.entries)
            values = await _segment(resolver, step, fill, context)
            for entry, value in zip(step.entries, values, strict=True):
                result[entry[0]] = value
        else:
            await _await_group(step.entries, context, result)
    if target is None:
        return result, None
    finish = partial(_finish_call, tail.entries, result, target)
    return result, await _segment(resolver, tail, finish, context)


def forget_dep_caches(**kwargs: object) -> None:
    """Drop the introspection memos, so a reloaded module leaves none behind.

    A saved `page.py` mints fresh function objects the memos would otherwise pin.
    """
    forget_introspection_caches()
    resolver.forget_caches()


class DependencyResolver:
    """Build keyword arguments for a callable by replaying its compiled plan.

    The plan cache entry is one tuple, swapped atomically under the GIL against races.
    """

    def __init__(
        self, *providers: ParameterProvider | RegisteredParameterProvider
    ) -> None:
        """Initialise with explicit providers or defer to the auto-registry."""
        self._dependency_callables: dict[str, Callable[..., Any]] = {}
        self._async_dependency_callables: dict[str, Callable[..., Any]] = {}
        # Reentrant, as building a provider may resolve through this resolver.
        self._lock = threading.RLock()
        self._head: list[ParameterProvider] = []
        self._auto: list[RegisteredParameterProvider] = []
        self._tail: list[ParameterProvider] = [_adopt_provider(p) for p in providers]
        self._providers: list[ParameterProvider] = list(self._tail)
        # Explicit providers make the registry moot, so sync only notes its version.
        self._explicit = bool(providers)
        self._registry_seen = -1
        # Addresses `remove_provider` dropped, keyed like the registry.
        self._suppressed: set[_Address] = set()
        self._syncing = False
        # Bumped on every `_providers` mutation, so stale plans recompile.
        self._providers_version = 0
        self._plan_cache: LruCache[IntrospectKey, tuple[int, InjectionPlan]] = (
            LruCache()
        )
        # Dependency callables filling nothing, checked by identity so a rebind is seen.
        self._leaf_dependencies: dict[str, Callable[..., Any]] = {}
        self._dependencies_version = 0
        self._aplan_cache: LruCache[IntrospectKey, tuple[int, int, AsyncPlan]] = (
            LruCache()
        )

    def _instantiate(
        self, cls: type[RegisteredParameterProvider]
    ) -> RegisteredParameterProvider:
        """Build one auto-registered provider, handing over the resolver when asked."""
        if "resolver" in cached_signature(cls).parameters:
            return cast("RegisteredParameterProvider", cast("Any", cls)(resolver=self))
        return cls()

    def _rebuild(self) -> None:
        """Publish the flat provider list and move the version plans carry.

        A fresh list, so a compile walking the previous one keeps a consistent snapshot.
        """
        self._providers = [*self._head, *self._auto, *self._tail]
        self._providers_version += 1

    def _sync_providers(self) -> None:
        """Rebuild the auto-registered instances whenever the registry has moved.

        A full resync rather than a tail delta, so a replaced class or a race is seen.
        """
        if self._registry_seen == provider_registry.version:
            return
        if self._explicit:
            self._registry_seen = provider_registry.version
            return
        with self._lock:
            version = provider_registry.version
            if self._registry_seen == version or self._syncing:
                # A provider built through this resolver re-enters here half-built.
                return
            self._syncing = True
            try:
                # An abstract intermediate base registers but has no instance.
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
            # Rebuild first, so no thread sees fresh-looking plans on a stale list.
            self._rebuild()
            self._registry_seen = version

    @property
    def providers(self) -> tuple[ParameterProvider, ...]:
        """Return the providers in claim order, synced with the auto-registry first."""
        self._sync_providers()
        return tuple(self._providers)

    def skips(self, param: inspect.Parameter) -> bool:
        """Return True for `self` / `cls` and variadic parameters.

        Public as the `compile_plan` predicate, which a subclass may widen.
        """
        return param.name in RECEIVER_NAMES or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )

    def _compile_plan(self, func: Callable[..., Any]) -> tuple[InjectionPlan, bool]:
        """Compile the plan for `func` and say whether it is settled enough to cache.

        Unresolved hints mark the plan provisional, and an async `func` is refused.
        """
        if is_async_callable(func):
            raise AsyncDependencyError(func)
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

        The version is read before the compile, so a racing list swap stamps it stale.
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

        Answered from the compiled plan, so a skipped parameter never matches.
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

        A coroutine function is kept for the async path, an async generator raises.
        """
        if _awaitable(callable_dep, name):
            self._async_dependency_callables[name] = callable_dep
            self._dependency_callables.pop(name, None)
        else:
            self._dependency_callables[name] = callable_dep
            self._async_dependency_callables.pop(name, None)
        self._leaf_dependencies.pop(name, None)
        self._dependencies_version += 1
        return callable_dep

    def forget_caches(self) -> None:
        """Drop the compiled plans and leaf notes, recompiled on the next resolve."""
        self._plan_cache.clear()
        self._aplan_cache.clear()
        self._leaf_dependencies.clear()

    def get_dependency(self, name: str) -> Callable[..., Any] | None:
        """Return the callable bound to `name`, or None when nothing is bound."""
        bound = self._dependency_callables.get(name)
        return self._async_dependency_callables.get(name) if bound is None else bound

    def dependencies(self) -> Mapping[str, Callable[..., Any]]:
        """Return a read-only snapshot of every named dependency and its callable."""
        return MappingProxyType(
            {**self._dependency_callables, **self._async_dependency_callables}
        )

    def unregister_dependency(self, name: str) -> None:
        """Drop the binding for `name`, tolerating a name that has none."""
        self._dependency_callables.pop(name, None)
        self._async_dependency_callables.pop(name, None)
        self._leaf_dependencies.pop(name, None)
        self._dependencies_version += 1

    def dependency(
        self, name: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Return a decorator that registers the callable under the given name."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_dependency(name, func)
            return func

        return decorator

    def resolve_callable_dependency(
        self,
        name: str,
        context: ResolutionContext,
        *,
        param: inspect.Parameter | None = None,
    ) -> object:
        """Resolve a named dependency with cycle-safe memoisation.

        An unregistered name raises at once rather than injecting `None`.
        """
        callable_dep = self._dependency_callables.get(name)
        if callable_dep is None:
            return self._bridge_unbound(name, context, param)

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
            # Nothing to inject cannot re-enter, so no stack or in-progress marker.
            value = callable_dep()
            context.cache.set(name, value)
            return value

        context.stack.append(name)
        context.cache.mark_in_progress(name)

        try:
            resolved = self.resolve(callable_dep, context)
            if not resolved:
                # No provider adds parameters, so an empty plan stays empty.
                self._leaf_dependencies[name] = callable_dep
            value = callable_dep(**resolved)
            context.cache.set(name, value)
            return value
        finally:
            # Every nested resolution pops what it pushed, so this name is on top.
            context.stack.pop()
            context.cache.unmark_in_progress(name)

    def _unknown(
        self, name: str, param: inspect.Parameter | None
    ) -> UnknownDependencyError:
        """Return the error for a name no binding of either colour serves."""
        return UnknownDependencyError(
            name,
            None if param is None else param.name,
            registered=(*self._dependency_callables, *self._async_dependency_callables),
        )

    def _bridge_unbound(
        self, name: str, context: ResolutionContext, param: inspect.Parameter | None
    ) -> object:
        """Resolve a name the sync dict misses through the bridge, raising when unbound.

        A finished value skips the bridge, an in-progress mark crosses it to the cycle.
        """
        if name not in self._async_dependency_callables:
            raise self._unknown(name, param)
        cached = context.cache.get(name)
        if cached is not _CACHE_MISS and cached is not _IN_PROGRESS:
            return cached
        return bridge(
            f"dependency {name!r}",
            lambda: self.aresolve_callable_dependency(name, context, param=param),
        )

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

        Matches by identity as dataclass providers compare equal. Suppresses auto ones.
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

        A missing dependency names the innermost callable carrying the `Depends`.
        """
        # The hit path is inlined, a miss is rare enough to afford the helper.
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
        self, func: Callable[..., Any], /, **context: object
    ) -> dict[str, Any]:
        """Resolve `func` from a loose kwargs mapping and build a context object.

        Reserved names are popped in place, leaving the URL kwargs in `**context`.
        """
        loose: dict[str, Any] = context
        cache_obj = loose.pop("_cache", None)
        request = loose.pop("request", None)
        form = loose.pop("form", None)
        cleaned_data = loose.pop("cleaned_data", None)
        context_data = loose.pop("_context_data", None) or {}
        stack = loose.pop("_stack", None) or []
        for reserved in _UNCLAIMED_RESERVED:
            loose.pop(reserved, None)

        resolution_context = ResolutionContext(
            request=request,
            form=form,
            url_kwargs=loose,
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

        The context is passed unfiltered, as providers refuse `RESERVED_KEYS` already.
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

    def _compile_async_plan(self, func: Callable[..., Any]) -> tuple[AsyncPlan, bool]:
        """Compile the async plan for `func` and say whether it is settled to cache.

        Unresolved hints mark the plan provisional, and an async generator is refused.
        """
        target = Colour.ASYNC if _awaitable(func) else Colour.BLOCKING
        try:
            sig = cached_signature(func)
        except (ValueError, TypeError):
            return empty_async_plan(target), True
        try:
            hints = cached_type_hints(func)
        except HINT_ERRORS:
            logger.debug(
                "Type hints of %s did not resolve, so its async plan reads the raw "
                "annotations and is compiled again on the next resolve.",
                _Described(func),
                exc_info=True,
            )
            plan = compile_async_plan(
                sig, {}, self._providers, self.skips, target, self._planned
            )
            return plan, False
        plan = compile_async_plan(
            sig, hints, self._providers, self.skips, target, self._planned
        )
        return plan, True

    def _aplan_for(self, func: Callable[..., Any]) -> AsyncPlan:
        """Return the async plan for `func`, recompiled once providers or bindings move.

        The versions are read before the compile, so a racing change stamps it stale.
        """
        self._sync_providers()
        providers_version = self._providers_version
        dependencies_version = self._dependencies_version
        key = introspect_key(func)
        try:
            entry = self._aplan_cache.get(key)
        except TypeError:
            return self._compile_async_plan(func)[0]
        if (
            entry is not None
            and entry[0] == providers_version
            and entry[1] == dependencies_version
        ):
            return entry[2]
        plan, settled = self._compile_async_plan(func)
        if settled:
            self._aplan_cache[key] = (providers_version, dependencies_version, plan)
        return plan

    def _planned(self, func: Callable[..., Any], key: str | None) -> AsyncPlan | None:
        """Return the async plan of `func` reached under `key`, None for an open cycle.

        A cycle a deferring provider may break is left to the runtime stack check.
        """
        mark = describe_callable(func) if key is None else key
        chain = _compiling.get()
        deferred = edge_deferred()
        keys = [named for named, _deferred in chain]
        if mark in keys:
            if not deferred and not any(edge for _named, edge in chain):
                raise DependencyCycleError([*keys[keys.index(mark) :], mark])
            return None
        token = _compiling.set((*chain, (mark, deferred)))
        try:
            return self._aplan_for(func)
        finally:
            _compiling.reset(token)

    def awaits(self, func: Callable[..., Any], *, key: str) -> bool:
        """Return whether calling `func` on the async path awaits anything.

        A cycle a deferring provider may break awaits, left to the runtime stack check.
        """
        plan = self._planned(func, key)
        return plan is None or plan.awaits

    def awaits_dependency(self, name: str) -> bool:
        """Return whether the binding of `name` awaits anything, False for none."""
        bound = self.get_dependency(name)
        return False if bound is None else self.awaits(bound, key=name)

    async def _amemo(
        self, key: str, compute: Callable[[], Coroutine[Any, Any, object]]
    ) -> object:
        """Return what `compute` makes of `key`, through the request memo if one is set.

        The memo shares a key the plans count twice or its owner declares.
        """
        memo = request_memo.get()
        if memo is None:
            return await compute()
        shared = memo.share_all or key in memo.shared or key in _shares.get()
        return await memo.scope.resolve(key, compute, shared=shared)

    async def aseed_sources(
        self, sources: tuple[Source, ...], context: ResolutionContext
    ) -> None:
        """Resolve through the request scope each of `sources` it shares, in order.

        A segment fills a private copy of the cache, so only the scope runs a key once.
        """
        memo = request_memo.get()
        if memo is None:
            return
        share_all = memo.share_all
        declared = memo.shared
        counted = _shares.get()
        for key, func in sources:
            if share_all or key in declared or key in counted:
                await self.acall_factory(func, key, context)

    async def aseed(self, func: Callable[..., Any], context: ResolutionContext) -> None:
        """Resolve the shared keys `func` reaches, for its sync resolve in a segment."""
        sources = self._aplan_for(func).sources
        if sources:
            await self.aseed_sources(sources, context)

    async def aresolve_callable_dependency(
        self,
        name: str,
        context: ResolutionContext,
        *,
        param: inspect.Parameter | None = None,
    ) -> object:
        """Resolve a named dependency on the async path, awaiting an async binding."""
        callable_dep = self.get_dependency(name)
        if callable_dep is None:
            raise self._unknown(name, param)
        return await self.acall_factory(callable_dep, name, context)

    async def acall_factory(
        self, factory: Callable[..., Any], key: str, context: ResolutionContext
    ) -> object:
        """Call `factory` on the async path once per cache and request memo under `key`.

        An in-progress mark is a sync resolution elsewhere, so it reads as a miss.
        """
        stack = context.stack
        if key in stack:
            raise DependencyCycleError([*stack[stack.index(key) :], key])
        cached = context.cache.get(key)
        if cached is not _CACHE_MISS and cached is not _IN_PROGRESS:
            return cached
        stack.append(key)
        try:
            value = await self._amemo(
                key, lambda: self.acall(factory, _branch(context))
            )
        finally:
            stack.pop()
        context.cache.set(key, value)
        return value

    async def aresolve(
        self, func: Callable[..., object], context: ResolutionContext
    ) -> dict[str, Any]:
        """Return keyword arguments for `func`, awaiting what its async plan awaits.

        The first call compiles the plan into its own cache, the sync plans stay apart.
        """
        plan = self._aplan_for(func)
        token = _enter_shares(plan)
        try:
            result, _value = await _areplay(self, plan, context, None)
        except UnknownDependencyError as exc:
            exc.attribute_to(func)
            raise
        finally:
            if token is not None:
                _shares.reset(token)
        return result

    @overload
    async def acall[T](
        self, func: Callable[..., Coroutine[Any, Any, T]], context: ResolutionContext
    ) -> T: ...

    @overload
    async def acall[T](
        self, func: Callable[..., T], context: ResolutionContext
    ) -> T: ...

    async def acall(
        self, func: Callable[..., Any], context: ResolutionContext
    ) -> object:
        """Call `func` with its resolved arguments and return its value, awaiting it.

        A sync `func` returning a coroutine has it awaited, other awaitables come back.
        """
        plan = self._aplan_for(func)
        target = None if plan.target is Colour.ASYNC else func
        token = _enter_shares(plan)
        try:
            kwargs, value = await _areplay(self, plan, context, target)
        except UnknownDependencyError as exc:
            exc.attribute_to(func)
            raise
        finally:
            if token is not None:
                _shares.reset(token)
        if target is None:
            return await func(**kwargs)
        if isinstance(value, Coroutine):
            return await value
        return value


# One holder every reference points at, so none keeps a resolver the setting replaced.
_holder = SimpleLazyObject(DependencyResolver)
resolver: DependencyResolver = cast("DependencyResolver", _holder)

_HANDLE: Final = instance_dict(_holder)
"""The instance dict the holder keeps `_wrapped` in, read without the proxy lookup.

Every swap writes the same dict, so a read here sees it at no `LazyObject` cost.
"""

RegisteredParameterProvider.resolver = resolver


def _configured_resolver_class() -> type[DependencyResolver]:
    """Return the class named by `NEXT_FRAMEWORK["DEPENDENCY_RESOLVER"]`."""
    return resolve_setting_class(
        "DEPENDENCY_RESOLVER",
        base=DependencyResolver,
        # The import helper would hit a half-initialised `next.deps` package.
        shipped=DependencyResolver,
        base_path="next.deps.DependencyResolver",
    )


def current_resolver() -> DependencyResolver:
    """Return the resolver object the holder stands for, building it on first read.

    Read on a render path, as an attribute off the holder is a forwarded call.
    """
    built = _HANDLE["_wrapped"]
    if built is empty:
        _holder._setup()
        built = _HANDLE["_wrapped"]
    return cast("DependencyResolver", built)


def apply_resolver_setting() -> None:
    """Put an instance of the configured resolver class behind the holder.

    A fresh object, so a subclass `__init__` runs and runtime registrations are dropped.
    """
    cls = _configured_resolver_class()
    if _holder._wrapped is not empty and type(_holder._wrapped) is cls:
        return
    _holder._wrapped = cls()


def reapply_resolver_setting(**kwargs: object) -> None:
    """Rebuild the resolver singleton when the framework settings change."""
    apply_resolver_setting()


ROUTES.attach(forget_dep_caches)
