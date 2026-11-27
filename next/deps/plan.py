"""Compile a per-callable injection plan from the providers' static verdicts."""

from __future__ import annotations

# The `type` aliases below evaluate when autodoc reads them, so their names import here.
import inspect
from collections import Counter
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from .context import ResolutionContext
from .introspect import prepared_parameter
from .providers import ParameterProvider
from .runner import run_isolated


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


type ParameterFiller = Callable[[ResolutionContext], object]
"""The call the replay makes to fill one claimed parameter."""

type ParameterPlan = tuple[
    str,
    tuple[ParameterProvider, ...],
    object,
    inspect.Parameter,
    ParameterFiller | None,
]
"""Name, candidates, fallback, parameter, and filler, replayed per call.

A filler stands for the terminal provider that compiled it, so a parameter no signature
settled is exactly one whose filler is None. A plain tuple rather than a named one,
because `UNPACK_SEQUENCE` takes its fast path only for an exact tuple.
"""

type InjectionPlan = tuple[ParameterPlan, ...]

type AsyncParameterFiller = Callable[[ResolutionContext], Awaitable[object]]
"""The call the async replay awaits to fill one parameter."""

type AwaitedEntry = tuple[str, AsyncParameterFiller]
"""Name and async filler of one parameter the async replay awaits."""

type Source = tuple[str, Callable[..., Any]]
"""A key a plan reaches and the callable that computes it, for when it is shared."""

type Reach = tuple[str | None, Callable[..., Any]]
"""A callable that filling a parameter calls, with its memo key or None for none."""

type _CompileHook = Callable[[inspect.Parameter], ParameterFiller | None]
type _AsyncCompileHook = Callable[[inspect.Parameter], AsyncParameterFiller | None]
type _BlockingHook = Callable[[inspect.Parameter], bool]

EMPTY_PLAN: InjectionPlan = ()


class Colour(Enum):
    """How the async replay fills a parameter or calls its target."""

    BLOCKING = "blocking"
    ASYNC = "async"


@dataclass(frozen=True, slots=True)
class Blocking:
    """Consecutive blocking entries, filled in order by one segment runner call.

    `sources` are the keys the entries reach, resolved first on the loop when shared.
    """

    entries: InjectionPlan
    sources: tuple[Source, ...] = ()
    keys: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Awaited:
    """Consecutive async entries, awaited together once there are two or more."""

    entries: tuple[AwaitedEntry, ...]


type AsyncStep = Blocking | Awaited


@dataclass(frozen=True, slots=True)
class AsyncPlan:
    """The async replay of one callable, its pure entries first and then its steps.

    Steps run in signature order, so each blocking segment sees the work before it.
    """

    pure: InjectionPlan
    steps: tuple[AsyncStep, ...]
    target: Colour
    awaits: bool
    names: tuple[str, ...] = ()
    reach: tuple[tuple[str, tuple[str, ...]], ...] = ()
    shared: frozenset[str] = frozenset()
    sources: tuple[Source, ...] = ()


type _Edge = tuple[str, Callable[..., Any], AsyncPlan | None]

type Planner = Callable[[Callable[..., Any], str | None], AsyncPlan | None]
"""The call compiling the plan of a reached callable, None for a cycle still open."""

_deferred: ContextVar[bool] = ContextVar("next_deps_deferred", default=False)


def _filler(provider: ParameterProvider, param: inspect.Parameter) -> ParameterFiller:
    """Return the single call that fills `param` for the provider owning it.

    A provider without a hook keeps the plain `resolve`, and an uncallable one raises.
    """
    hook: _CompileHook | None = getattr(provider, "compile_resolve", None)
    if callable(hook):
        # Widened from the declared return to guard against a third-party hook.
        compiled: object = hook(param)
        if compiled is not None:
            if not callable(compiled):
                msg = (
                    f"{type(provider).__name__}.compile_resolve returned "
                    f"{compiled!r} for parameter {param.name!r}, expected a "
                    "callable or None."
                )
                raise TypeError(msg)
            return cast("ParameterFiller", compiled)
    return partial(provider.resolve, param)


def _claim(
    name: str, param: inspect.Parameter, providers: Sequence[ParameterProvider]
) -> tuple[tuple[ParameterProvider, ...], ParameterProvider | None]:
    """Return the providers deferring on `param` and the one claiming it for good.

    A third-party verdict outside the three-valued contract raises instead of guessing.
    """
    candidates: list[ParameterProvider] = []
    for provider in providers:
        verdict: object = provider.static_can_handle(param)
        if verdict is False:
            continue
        if verdict is None:
            candidates.append(provider)
            continue
        if verdict is not True:
            msg = (
                f"{type(provider).__name__}.static_can_handle returned "
                f"{verdict!r} for parameter {name!r}, expected True, False, "
                "or None."
            )
            raise TypeError(msg)
        return tuple(candidates), provider
    return tuple(candidates), None


def compile_plan(
    signature: inspect.Signature,
    hints: Mapping[str, Any],
    providers: Sequence[ParameterProvider],
    skips: Callable[[inspect.Parameter], bool],
) -> InjectionPlan:
    """Return one plan entry per injectable parameter of `signature`."""
    entries: list[ParameterPlan] = []
    for name, raw in signature.parameters.items():
        if skips(raw):
            continue
        param, fallback = prepared_parameter(name, raw, hints)
        candidates, terminal = _claim(name, param, providers)
        filler = None if terminal is None else _filler(terminal, param)
        entries.append((name, candidates, fallback, param, filler))
    return tuple(entries)


def _async_filler(
    provider: ParameterProvider, param: inspect.Parameter
) -> AsyncParameterFiller | None:
    """Return the async filler the provider offers for `param`, or None for none."""
    hook: _AsyncCompileHook | None = getattr(provider, "compile_aresolve", None)
    if not callable(hook):
        return None
    compiled: object = hook(param)
    if compiled is None:
        return None
    if not callable(compiled):
        msg = (
            f"{type(provider).__name__}.compile_aresolve returned {compiled!r} "
            f"for parameter {param.name!r}, expected a callable or None."
        )
        raise TypeError(msg)
    return cast("AsyncParameterFiller", compiled)


def _blocks(provider: ParameterProvider, param: inspect.Parameter) -> bool:
    """Return whether filling `param` through `provider` may block the loop.

    A provider declaring nothing blocks, which is the safe reading of an unknown one.
    """
    hook: _BlockingHook | None = getattr(provider, "static_blocking", None)
    if callable(hook):
        return bool(hook(param))
    return bool(getattr(provider, "blocking", True))


def replay(entry: ParameterPlan, context: ResolutionContext) -> object:
    """Fill one plan entry the way the sync replay does, outside its inlined loop."""
    _name, candidates, fallback, param, filler = entry
    for provider in candidates:
        if provider.can_handle(param, context):
            return provider.resolve(param, context)
    return fallback if filler is None else filler(context)


def _awaiting(
    param: inspect.Parameter,
    candidates: tuple[ParameterProvider, ...],
    afiller: AsyncParameterFiller,
) -> AsyncParameterFiller:
    """Return the async filler of `param`, asking its deferring providers first."""
    if not candidates:
        return afiller
    claimants = tuple((provider, _blocks(provider, param)) for provider in candidates)

    async def fill(context: ResolutionContext) -> object:
        for provider, blocks in claimants:
            if provider.can_handle(param, context):
                if blocks:
                    return await run_isolated(partial(provider.resolve, param), context)
                return provider.resolve(param, context)
        return await afiller(context)

    return fill


def _segment_after(
    steps: list[AsyncStep], entry: ParameterPlan, sources: tuple[Source, ...]
) -> None:
    """Add a blocking entry to the segment ending `steps`, opening one when needed."""
    last = steps[-1] if steps else None
    if isinstance(last, Blocking):
        if sources:
            joined = _joined(last.sources, sources)
            keys = last.keys.union(key for key, _func in sources)
            steps[-1] = Blocking((*last.entries, entry), joined, keys)
        else:
            steps[-1] = Blocking((*last.entries, entry), last.sources, last.keys)
    else:
        keys = frozenset(key for key, _func in sources)
        steps.append(Blocking((entry,), sources, keys))


def _awaited_after(steps: list[AsyncStep], entry: AwaitedEntry) -> None:
    """Add an async entry to the group ending `steps`, opening one when needed."""
    last = steps[-1] if steps else None
    if isinstance(last, Awaited):
        steps[-1] = Awaited((*last.entries, entry))
    else:
        steps.append(Awaited((entry,)))


def empty_async_plan(target: Colour) -> AsyncPlan:
    """Return the plan of a callable with nothing to inject."""
    return AsyncPlan((), (), target, awaits=target is Colour.ASYNC)


def _joined(
    first: tuple[Source, ...], second: tuple[Source, ...]
) -> tuple[Source, ...]:
    """Return the sources of both, each key once and in first-reached order."""
    keys = {key for key, _func in first}
    return (*first, *(source for source in second if source[0] not in keys))


def edge_deferred() -> bool:
    """Return whether a deferring provider may claim the parameter compiling now."""
    return _deferred.get()


def _reaches(
    provider: ParameterProvider, param: inspect.Parameter
) -> tuple[Reach, ...]:
    """Return what `provider` reports that filling `param` calls, none when silent."""
    hook: Callable[[inspect.Parameter], tuple[Reach, ...]] | None = getattr(
        provider, "static_reach", None
    )
    return () if hook is None else tuple(hook(param))


class _Reached:
    """The keys and the inline calls the parameters of one signature reach."""

    __slots__ = ("edges", "inlined", "planner")

    def __init__(self, planner: Planner) -> None:
        """Start with nothing reached, compiling reached callables through `planner`."""
        self.planner = planner
        self.edges: list[_Edge] = []
        self.inlined: list[AsyncPlan] = []

    def offered(
        self,
        terminal: ParameterProvider | None,
        param: inspect.Parameter,
        candidates: tuple[ParameterProvider, ...],
    ) -> tuple[AsyncParameterFiller | None, tuple[Source, ...]]:
        """Ask `terminal` for an async filler and the keys filling `param` reaches.

        A deferring candidate may claim the parameter, so none of its keys is certain.
        """
        if terminal is None:
            return None, ()
        token = _deferred.set(bool(candidates))
        try:
            afiller = _async_filler(terminal, param)
            found = self._reach(terminal, param)
        finally:
            _deferred.reset(token)
        return afiller, () if candidates else found

    def _reach(
        self, provider: ParameterProvider, param: inspect.Parameter
    ) -> tuple[Source, ...]:
        """Record what `provider` reaches for `param`, each key after its own keys.

        A plan still compiling closes a cycle a deferring provider breaks, none is kept.
        """
        found: tuple[Source, ...] = ()
        for key, func in _reaches(provider, param):
            plan = self.planner(func, key)
            if key is not None:
                self.edges.append((key, func, plan))
                if plan is not None:
                    found = _joined(found, (*plan.sources, (key, func)))
            elif plan is not None:
                self.inlined.append(plan)
                found = _joined(found, plan.sources)
        return found

    def shared(self, plan: AsyncPlan) -> AsyncPlan:
        """Return `plan` with the keys it and each consumer it reaches name.

        A consumer counts once however many paths reach it, and two counts share a key.
        """
        edges = self.edges
        if not edges and not self.inlined:
            return plan
        names = [key for key, _func, _named in edges]
        reach: dict[str, tuple[str, ...]] = {}
        for inline in self.inlined:
            names.extend(inline.names)
            reach.update(inline.reach)
        for key, _func, named in edges:
            if named is not None:
                reach.update(named.reach)
                reach[key] = named.names
        counts = Counter(names)
        for consumed in reach.values():
            counts.update(consumed)
        shared = frozenset(key for key, count in counts.items() if count > 1)
        return replace(
            plan, names=tuple(names), reach=tuple(reach.items()), shared=shared
        )


def compile_async_plan(
    signature: inspect.Signature,
    hints: Mapping[str, Any],
    providers: Sequence[ParameterProvider],
    skips: Callable[[inspect.Parameter], bool],
    target: Colour,
    planner: Planner,
) -> AsyncPlan:
    """Return the async plan of `signature`, each entry classified once.

    An async filler makes an entry async, any blocking provider makes it blocking.
    """
    pure: list[ParameterPlan] = []
    steps: list[AsyncStep] = []
    sources: tuple[Source, ...] = ()
    reached = _Reached(planner)
    for name, raw in signature.parameters.items():
        if skips(raw):
            continue
        param, fallback = prepared_parameter(name, raw, hints)
        candidates, terminal = _claim(name, param, providers)
        afiller, found = reached.offered(terminal, param, candidates)
        if found:
            sources = _joined(sources, found)
        if afiller is not None:
            _awaited_after(steps, (name, _awaiting(param, candidates, afiller)))
            continue
        filler = None if terminal is None else _filler(terminal, param)
        entry: ParameterPlan = (name, candidates, fallback, param, filler)
        blocking = any(_blocks(provider, param) for provider in candidates) or (
            terminal is not None and _blocks(terminal, param)
        )
        if blocking:
            _segment_after(steps, entry, found)
        else:
            pure.append(entry)
    awaits = target is Colour.ASYNC or any(isinstance(s, Awaited) for s in steps)
    plan = AsyncPlan(tuple(pure), tuple(steps), target, awaits=awaits, sources=sources)
    return reached.shared(plan)


__all__ = [
    "EMPTY_PLAN",
    "AsyncParameterFiller",
    "AsyncPlan",
    "AsyncStep",
    "Awaited",
    "AwaitedEntry",
    "Blocking",
    "Colour",
    "InjectionPlan",
    "ParameterFiller",
    "ParameterPlan",
    "Planner",
    "Reach",
    "Source",
    "compile_async_plan",
    "compile_plan",
    "edge_deferred",
    "empty_async_plan",
    "replay",
]
