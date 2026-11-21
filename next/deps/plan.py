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

type _CompileHook = Callable[[inspect.Parameter], ParameterFiller | None]
type _AsyncCompileHook = Callable[[inspect.Parameter], AsyncParameterFiller | None]
type _BlockingHook = Callable[[inspect.Parameter], bool]

EMPTY_PLAN: InjectionPlan = ()


class Colour(Enum):
    """How the async replay fills a parameter or calls its target."""

    PURE = "pure"
    BLOCKING = "blocking"
    ASYNC = "async"


@dataclass(frozen=True, slots=True)
class Blocking:
    """Consecutive blocking entries, filled in order by one segment runner call."""

    entries: InjectionPlan


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

    colours: tuple[tuple[str, Colour], ...]
    pure: InjectionPlan
    steps: tuple[AsyncStep, ...]
    target: Colour
    awaits: bool
    names: tuple[str, ...] = ()
    reach: tuple[tuple[str, tuple[str, ...]], ...] = ()
    shared: frozenset[str] = frozenset()


type _Edge = tuple[str, AsyncPlan | None]

_edges: ContextVar[list[_Edge] | None] = ContextVar("_edges", default=None)
_deferred: ContextVar[bool] = ContextVar("_deferred", default=False)


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


def _segment_after(steps: list[AsyncStep], entry: ParameterPlan) -> None:
    """Add a blocking entry to the segment ending `steps`, opening one when needed."""
    last = steps[-1] if steps else None
    if isinstance(last, Blocking):
        steps[-1] = Blocking((*last.entries, entry))
    else:
        steps.append(Blocking((entry,)))


def _awaited_after(steps: list[AsyncStep], entry: AwaitedEntry) -> None:
    """Add an async entry to the group ending `steps`, opening one when needed."""
    last = steps[-1] if steps else None
    if isinstance(last, Awaited):
        steps[-1] = Awaited((*last.entries, entry))
    else:
        steps.append(Awaited((entry,)))


def empty_async_plan(target: Colour) -> AsyncPlan:
    """Return the plan of a callable with nothing to inject."""
    return AsyncPlan((), (), (), target, awaits=target is Colour.ASYNC)


def name_key(key: str, plan: AsyncPlan | None) -> None:
    """Note that the plan compiling now names `key`, its plan None while it compiles."""
    edges = _edges.get()
    if edges is not None:
        edges.append((key, plan))


def edge_deferred() -> bool:
    """Return whether a deferring provider may claim the parameter compiling now."""
    return _deferred.get()


def _offered(
    terminal: ParameterProvider | None,
    param: inspect.Parameter,
    candidates: tuple[ParameterProvider, ...],
) -> AsyncParameterFiller | None:
    """Ask `terminal` for an async filler, noting whether a candidate defers."""
    if terminal is None:
        return None
    token = _deferred.set(bool(candidates))
    try:
        return _async_filler(terminal, param)
    finally:
        _deferred.reset(token)


def _sharing(plan: AsyncPlan, edges: list[_Edge]) -> AsyncPlan:
    """Return `plan` with the keys it names and the keys each consumer it reaches names.

    Each consumer counts once however many paths reach it, and two counts share a key.
    """
    names = tuple(key for key, _named in edges)
    reach: dict[str, tuple[str, ...]] = {}
    for key, named in edges:
        if named is not None:
            reach.update(named.reach)
            reach[key] = named.names
    counts = Counter(names)
    for consumed in reach.values():
        counts.update(consumed)
    shared = frozenset(key for key, count in counts.items() if count > 1)
    return replace(plan, names=names, reach=tuple(reach.items()), shared=shared)


def compile_async_plan(
    signature: inspect.Signature,
    hints: Mapping[str, Any],
    providers: Sequence[ParameterProvider],
    skips: Callable[[inspect.Parameter], bool],
    target: Colour,
) -> AsyncPlan:
    """Return the async plan of `signature`, each entry classified once.

    An async filler makes an entry async, any blocking provider makes it blocking.
    """
    colours: list[tuple[str, Colour]] = []
    pure: list[ParameterPlan] = []
    steps: list[AsyncStep] = []
    edges: list[_Edge] = []
    token = _edges.set(edges)
    try:
        for name, raw in signature.parameters.items():
            if skips(raw):
                continue
            param, fallback = prepared_parameter(name, raw, hints)
            candidates, terminal = _claim(name, param, providers)
            afiller = _offered(terminal, param, candidates)
            if afiller is not None:
                colours.append((name, Colour.ASYNC))
                _awaited_after(steps, (name, _awaiting(param, candidates, afiller)))
                continue
            filler = None if terminal is None else _filler(terminal, param)
            entry: ParameterPlan = (name, candidates, fallback, param, filler)
            blocking = any(_blocks(provider, param) for provider in candidates) or (
                terminal is not None and _blocks(terminal, param)
            )
            if blocking:
                colours.append((name, Colour.BLOCKING))
                _segment_after(steps, entry)
            else:
                colours.append((name, Colour.PURE))
                pure.append(entry)
    finally:
        _edges.reset(token)
    awaits = target is Colour.ASYNC or any(isinstance(s, Awaited) for s in steps)
    plan = AsyncPlan(tuple(colours), tuple(pure), tuple(steps), target, awaits=awaits)
    return _sharing(plan, edges) if edges else plan


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
    "compile_async_plan",
    "compile_plan",
    "edge_deferred",
    "empty_async_plan",
    "name_key",
    "replay",
]
