"""Fill a callable's parameters from the context by replaying its compiled plan.

`DependencyResolver` is the orchestrator consumed by page views, form actions,
and component renderers. Each callable is compiled once into an `InjectionPlan`
from the providers' static verdicts, and every resolve replays that plan
instead of scanning the provider list.
"""

from __future__ import annotations

import inspect
import logging
import threading
from collections import OrderedDict
from difflib import get_close_matches
from operator import attrgetter
from types import MethodType
from typing import TYPE_CHECKING, Any, ClassVar, cast, get_type_hints, override

from next.utils import callable_name, code_filename, store_bounded

from .cache import _CACHE_MISS, _IN_PROGRESS, DependencyCache, DependencyCycleError
from .context import RESERVED_KEYS, ResolutionContext
from .plan import EMPTY_PLAN, InjectionPlan, compile_plan
from .providers import ParameterProvider, RegisteredParameterProvider
from .registry import provider_registry


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from django.http import HttpRequest


logger = logging.getLogger(__name__)


type _IntrospectKey = tuple[object, bool]

# Bounded because the dev reloader re-executes a `page.py` on every save and
# mints fresh function objects, each of which would otherwise stay pinned here
# for the life of the process.
_INTROSPECTION_CACHE_MAX_SIZE = 2048

# Everything an annotation expression is allowed to fail with. A string hint
# raises from the parser as readily as from the lookup, and under deferred
# evaluation the expression itself runs at this point.
_HINT_ERRORS: tuple[type[Exception], ...] = (
    NameError,
    TypeError,
    AttributeError,
    ValueError,
    SyntaxError,
    KeyError,
    ImportError,
    RecursionError,
)

# Keyed by the stable `__func__` plus a bound flag, because a bound method is
# recreated on each access.
_signature_cache: OrderedDict[_IntrospectKey, inspect.Signature] = OrderedDict()
_type_hints_cache: OrderedDict[_IntrospectKey, dict[str, Any]] = OrderedDict()
_var_keyword_cache: OrderedDict[_IntrospectKey, bool] = OrderedDict()


def _describe_callable(func: Callable[..., Any]) -> str:
    """Return a human-readable name and source path for `func` in error messages."""
    name = callable_name(func)
    filename = code_filename(func)
    return f'"{name}"' if filename is None else f'"{name}" ({filename})'


class UnknownDependencyError(LookupError):
    """Raised when `Depends` names a dependency that nothing has registered."""

    def __init__(
        self, name: str, param_name: str | None, *, registered: Iterable[str] = ()
    ) -> None:
        """Record the missing name, the parameter, and the closest registered name.

        The callable being filled is attached afterwards by the resolve that
        failed, so a successful replay owns no callable and pays nothing.
        """
        self.name = name
        self.param_name = param_name
        self.func: Callable[..., Any] | None = None
        matches = get_close_matches(name, registered, n=1)
        self.suggestion: str | None = str(matches[0]) if matches else None
        super().__init__(name)

    def attribute_to(self, func: Callable[..., Any]) -> None:
        """Name `func` as the owner unless an inner resolve already named one."""
        if self.func is None:
            self.func = func

    @override
    def __str__(self) -> str:
        """Compose the message from the name, the parameter, and the owner.

        A registered name close to the missing one replaces the generic advice,
        because a near miss is almost always a typo at the `Depends` site.
        """
        where = "" if self.param_name is None else f' on parameter "{self.param_name}"'
        owner = "" if self.func is None else f" of {_describe_callable(self.func)}"
        advice = (
            f'Register it with resolver.dependency("{self.name}") or fix the name.'
            if self.suggestion is None
            else f'Did you mean "{self.suggestion}"?'
        )
        return (
            f'Depends("{self.name}"){where}{owner} names a dependency nothing '
            f"registered. {advice}"
        )


def _adopt_provider[P](provider: P) -> P:
    """Return `provider` once it carries the hook the plan compiler calls.

    Checked as the provider joins a resolver, so one written against an older
    contract names itself here instead of surfacing as an `AttributeError` out
    of the compiler on an unrelated callable.
    """
    if not callable(getattr(provider, "static_can_handle", None)):
        msg = (
            f"{type(provider).__name__} defines no callable static_can_handle, "
            "which every parameter provider owes the plan compiler. Implement "
            "it to classify a parameter from its signature alone and return "
            "None for a verdict that depends on the context."
        )
        raise TypeError(msg)
    return provider


def _introspect_key(func: Callable[..., Any]) -> _IntrospectKey:
    # `isinstance` over `inspect.ismethod`, which is a Python wrapper around the
    # same check and sits on every memoised lookup and plan replay.
    if isinstance(func, MethodType):
        return (func.__func__, True)
    return (func, False)


def cached_signature(func: Callable[..., Any]) -> inspect.Signature:
    """Return `inspect.signature(func)`, memoised per callable."""
    key = _introspect_key(func)
    try:
        cached = _signature_cache.get(key)
    except TypeError:
        # A callable no mapping can key is inspected afresh rather than refused.
        return inspect.signature(func)
    if cached is None:
        cached = inspect.signature(func)
        store_bounded(_signature_cache, key, cached, _INTROSPECTION_CACHE_MAX_SIZE)
    else:
        _signature_cache.move_to_end(key)
    return cached


def cached_type_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """Return the type hints of `func` with the `Annotated` extras kept, memoised."""
    key = _introspect_key(func)
    try:
        cached = _type_hints_cache.get(key)
    except TypeError:
        return get_type_hints(func, include_extras=True)
    if cached is None:
        cached = get_type_hints(func, include_extras=True)
        store_bounded(_type_hints_cache, key, cached, _INTROSPECTION_CACHE_MAX_SIZE)
    else:
        _type_hints_cache.move_to_end(key)
    return cached


def cached_accepts_var_keyword(func: Callable[..., Any]) -> bool:
    """Return whether `func` declares a `**kwargs` parameter, memoised per callable."""
    key = _introspect_key(func)
    try:
        cached = _var_keyword_cache.get(key)
    except TypeError:
        return _accepts_var_keyword(func)
    if cached is None:
        cached = _accepts_var_keyword(func)
        store_bounded(_var_keyword_cache, key, cached, _INTROSPECTION_CACHE_MAX_SIZE)
    else:
        _var_keyword_cache.move_to_end(key)
    return cached


def _accepts_var_keyword(func: Callable[..., Any]) -> bool:
    """Return whether the signature of `func` ends in a `**kwargs` parameter."""
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in cached_signature(func).parameters.values()
    )


def forget_dep_caches(**kwargs) -> None:
    """Drop every memo keyed by a callable, so a reloaded module leaves none behind.

    A saved `page.py` mints fresh function objects, and the memos would
    otherwise pin every generation of them together with their globals.
    """
    _signature_cache.clear()
    _type_hints_cache.clear()
    _var_keyword_cache.clear()
    resolver._plan_cache.clear()


class DependencyResolver:
    """Build keyword arguments for a callable by replaying its compiled plan.

    The plan is cached per callable together with the providers version it was
    compiled against. The cache entry is one tuple swapped atomically under the
    GIL, so a concurrent first call at worst compiles the same plan twice.
    """

    EXPLICIT_RESOLVE_KEYS: ClassVar[frozenset[str]] = RESERVED_KEYS

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
        # Bumped on every `_providers` mutation, so a compiled plan carries the
        # version it saw and is recompiled once the list has moved on.
        self._providers_version = 0
        self._plan_cache: OrderedDict[_IntrospectKey, tuple[int, InjectionPlan]] = (
            OrderedDict()
        )

    def _instantiate(
        self, cls: type[RegisteredParameterProvider]
    ) -> RegisteredParameterProvider:
        """Build one auto-registered provider, handing over the resolver when asked."""
        if "resolver" in inspect.signature(cls).parameters:
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

        A full resync rather than a tail delta, because a class the reloader
        replaced, one that refuses to instantiate, and two threads arriving at
        once all have to end at the list the registry describes.
        """
        if self._registry_seen == provider_registry._version:
            return
        if self._explicit:
            self._registry_seen = provider_registry._version
            return
        with self._lock:
            version = provider_registry._version
            if self._registry_seen == version:
                return
            # An abstract intermediate base is a legitimate class to register
            # and no instance of it exists to place.
            auto = [
                _adopt_provider(self._instantiate(cls))
                for cls in provider_registry
                if not inspect.isabstract(cls)
            ]
            auto.sort(key=attrgetter("priority"))
            self._auto = auto
            self._registry_seen = version
            self._rebuild()

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

        Hints that do not resolve leave the raw annotations in place and mark
        the plan provisional, so a name only a later import defines is picked
        up on the next resolve instead of being frozen out for good.
        """
        try:
            sig = cached_signature(func)
        except (ValueError, TypeError):
            return EMPTY_PLAN, True
        try:
            hints = cached_type_hints(func)
        except _HINT_ERRORS:
            logger.debug(
                "Type hints of %s did not resolve, so its plan reads the raw "
                "annotations and is compiled again on the next resolve.",
                _describe_callable(func),
                exc_info=True,
            )
            return compile_plan(sig, {}, self._providers, self.skips), False
        return compile_plan(sig, hints, self._providers, self.skips), True

    def _plan_for(self, key: _IntrospectKey, func: Callable[..., Any]) -> InjectionPlan:
        """Return the plan for `func` under `key`, recompiling once the providers moved.

        The version is read before the compile, so a plan built from a list
        another thread was replacing is stamped stale rather than fresh.
        """
        self._sync_providers()
        version = self._providers_version
        entry = self._plan_cache.get(key)
        if entry is not None and entry[0] == version:
            self._plan_cache.move_to_end(key)
            return entry[1]
        plan, settled = self._compile_plan(func)
        if settled:
            store_bounded(
                self._plan_cache, key, (version, plan), _INTROSPECTION_CACHE_MAX_SIZE
            )
        return plan

    def _plan(self, func: Callable[..., Any]) -> InjectionPlan:
        """Return the plan for `func`, compiling afresh when no mapping can key it."""
        key = _introspect_key(func)
        try:
            hash(key)
        except TypeError:
            self._sync_providers()
            return self._compile_plan(func)[0]
        return self._plan_for(key, func)

    def provides(
        self,
        func: Callable[..., Any],
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Return whether a registered provider fills `param` of `func` in `context`.

        The answer comes from the compiled plan, so a parameter the plan skips
        or one outside the signature is never filled, and the candidates see
        the same resolved parameter a resolve hands them.
        `EXPLICIT_RESOLVE_KEYS` is left to the caller.
        """
        entry = next((e for e in self._plan(func) if e[0] == param.name), None)
        if entry is None:
            return False
        _name, candidates, terminal, _fallback, resolved = entry
        if terminal is not None:
            return True
        return any(provider.can_handle(resolved, context) for provider in candidates)

    def register_dependency(
        self, name: str, callable_dep: Callable[..., Any]
    ) -> Callable[..., Any]:
        """Register a callable as a dependency reachable through `Depends("name")`."""
        self._dependency_callables[name] = callable_dep
        return callable_dep

    def get_dependency(self, name: str) -> Callable[..., Any] | None:
        """Return the callable bound to `name`, or None when nothing is bound."""
        return self._dependency_callables.get(name)

    def unregister_dependency(self, name: str) -> None:
        """Drop the binding for `name`, tolerating a name that has none."""
        self._dependency_callables.pop(name, None)

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

        By identity rather than equality, because two providers of a dataclass
        type compare equal and the list would give up the wrong instance.
        """
        with self._lock:
            for holder in (self._head, self._tail, self._auto):
                index = next(
                    (i for i, held in enumerate(holder) if held is provider), None
                )
                if index is not None:
                    del holder[index]
                    self._rebuild()
                    return

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

        A missing dependency takes the name of the callable filled here, which
        is the innermost one of a nested chain and therefore the one whose
        signature carries the offending `Depends`.
        """
        # The hit path is inlined, a miss is rare enough to afford the helper.
        key = _introspect_key(func)
        try:
            entry = self._plan_cache.get(key)
        except TypeError:
            # A callable no mapping can key compiles a plan per resolve rather
            # than losing injection altogether.
            self._sync_providers()
            plan = self._compile_plan(func)[0]
        else:
            if (
                entry is None
                or entry[0] != self._providers_version
                or self._registry_seen != provider_registry._version
            ):
                plan = self._plan_for(key, func)
            else:
                self._plan_cache.move_to_end(key)
                plan = entry[1]
        if not plan:
            return {}

        result: dict[str, Any] = {}
        try:
            for name, candidates, terminal, fallback, param in plan:
                for provider in candidates:
                    if provider.can_handle(param, context):
                        result[name] = provider.resolve(param, context)
                        break
                else:
                    result[name] = (
                        fallback
                        if terminal is None
                        else terminal.resolve(param, context)
                    )
        except UnknownDependencyError as exc:
            exc.attribute_to(func)
            raise
        return result

    def resolve_dependencies(
        self, func: Callable[..., Any], **context
    ) -> dict[str, Any]:
        """Resolve `func` from a loose kwargs mapping and build a context object."""
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
            request=context.get("request"),
            form=context.get("form"),
            url_kwargs=url_kwargs,
            context_data=context.get("_context_data") or {},
            cache=cache,
            stack=context.get("_stack") or [],
            cleaned_data=context.get("cleaned_data"),
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

        The template context travels as it is, because the providers that read
        it by name refuse `EXPLICIT_RESOLVE_KEYS` themselves. That costs one
        test per parameter rather than a copy of the whole context per render.
        """
        if isinstance(_cache, dict):
            cache = DependencyCache(backing_dict=_cache)
        elif isinstance(_cache, DependencyCache):
            cache = _cache
        else:
            cache = DependencyCache()

        context = ResolutionContext(
            request=request,
            form=None if template_context is None else template_context.get("form"),
            url_kwargs={},
            context_data={} if template_context is None else template_context,
            cache=cache,
            stack=_stack or [],
        )

        return self.resolve(func, context)


resolver: DependencyResolver = DependencyResolver()

RegisteredParameterProvider.resolver = resolver
