"""Compile a per-callable injection plan from the providers' static verdicts.

A marker parameter is owned by one provider no matter the context, so the
per-request scan of every provider is pure overhead for it. The compiler asks
each provider once, keeps only the ones the signature cannot rule out, and the
resolver replays that short-list on every call.
"""

from __future__ import annotations

import inspect
from functools import partial
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from .context import ResolutionContext
    from .providers import ParameterProvider


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

type _CompileHook = Callable[[inspect.Parameter], ParameterFiller | None]

EMPTY_PLAN: InjectionPlan = ()


def _filler(provider: ParameterProvider, param: inspect.Parameter) -> ParameterFiller:
    """Return the single call that fills `param` for the provider owning it.

    The hook is read off the instance, so a provider that defines none keeps the plain
    `resolve` path. The bound method is captured here either way, not on every replay.
    A hook that compiles something uncallable raises here, where the provider is named,
    rather than on every replay of the plan it went into.
    """
    hook: _CompileHook | None = getattr(provider, "compile_resolve", None)
    if callable(hook):
        # Widened from the declared return, because the compiler guards against a
        # third-party hook that hands back something no replay could call.
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


def compile_plan(
    signature: inspect.Signature,
    hints: Mapping[str, Any],
    providers: Sequence[ParameterProvider],
    skips: Callable[[inspect.Parameter], bool],
) -> InjectionPlan:
    """Return one plan entry per injectable parameter of `signature`.

    Providers are walked in list order, which is already sorted by priority
    and keeps custom insertions where they were put. A verdict outside the
    three-valued contract raises from the compile rather than changing injection
    semantics silently. The walk reaches every parameter of every callable, so one
    such provider raises for all of them until it is fixed.
    """
    entries: list[ParameterPlan] = []
    empty = inspect.Parameter.empty
    for name, raw in signature.parameters.items():
        if skips(raw):
            continue
        annotation = hints.get(name, raw.annotation)
        param = raw
        if annotation is not raw.annotation:
            param = raw.replace(annotation=annotation)
        candidates: list[ParameterProvider] = []
        terminal: ParameterProvider | None = None
        for provider in providers:
            # Widened from the declared verdict, because the compiler guards
            # against a third-party hook that returns something else.
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
            terminal = provider
            break
        fallback = None if param.default is empty else param.default
        filler = None if terminal is None else _filler(terminal, param)
        entries.append((name, tuple(candidates), fallback, param, filler))
    return tuple(entries)


__all__ = [
    "EMPTY_PLAN",
    "InjectionPlan",
    "ParameterFiller",
    "ParameterPlan",
    "compile_plan",
]
